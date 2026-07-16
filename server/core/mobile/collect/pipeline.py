"""手机采集任务运行时 — 流式 Pipeline。

统一编排: 应用 → 搜索 → 截屏(滑动) → 分析(结构化) → 增量入库 → 增量通知。
- collect 阶段单 worker 串行(物理设备独占), 下游 analyze/persist/notify 并发;
- 通过 stop_event 协作式取消, 保证设备释放。
"""
from __future__ import annotations

import asyncio
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.logger import get_logger
from core.observability import obs_log
from core.stream import Pipeline, Stage, Item, RetryPolicy

from core.mobile.manager import MobileDeviceManager
from core.mobile.coordinates import resolve_swipe, resolve_tap
from core.mobile.screen_capture import capture_ready_screen
from core.mobile.planner import run_planned_task
from core.mobile.identity import resolve_device_key
from core.mobile.pool import DevicePool, PoolError
from core.mobile.collect.analysis import (
    analyze_screenshot,
    triage_screenshot,
    analyze_detail,
)
from core.mobile.collect.source_links import extract_source_link

from api.dao import mobile_collect as collect_dao
from api.dao import mobile_artifacts as ma_dao
from api.dao import findings as findings_dao
from api.models.mobile_collect import ExtractField
from core.mobile.collect.contacts import (
    extract_contacts,
    record_text_blob,
    build_contact_findings,
    grade_with_contacts,
)

logger = get_logger("mobile_collect")

_OBS_SOURCE = "mobile_collect"

# 运行中任务的停止信号: run_task_id -> Event
_running: dict[str, asyncio.Event] = {}


def request_stop(run_task_id: str) -> bool:
    """请求停止一个运行中的采集任务。返回是否命中。"""
    ev = _running.get(run_task_id)
    if ev is None:
        return False
    ev.set()
    return True


def is_running(run_task_id: str) -> bool:
    return run_task_id in _running


def _do_swipe(device_id: str) -> None:
    """向上滑动一屏(与 command_executor 的浏览滑动一致)。"""
    mgr = MobileDeviceManager()
    dev = mgr.get_device(device_id)
    adb_id = mgr.resolve_adb_device_id(device_id)
    sx, sy, ex, ey = resolve_swipe(
        500, 780, 500, 260, device_id=adb_id, coord_space="normalized_1000"
    )
    dev.swipe(sx, sy, ex, ey, 450, delay=0.1)


def _do_tap(device_id: str, nx: int, ny: int) -> None:
    """点击 0-1000 归一化坐标。"""
    mgr = MobileDeviceManager()
    dev = mgr.get_device(device_id)
    adb_id = mgr.resolve_adb_device_id(device_id)
    px, py = resolve_tap(nx, ny, device_id=adb_id, coord_space="normalized_1000")
    dev.tap(px, py, delay=0.1)


def _do_back(device_id: str) -> None:
    """返回上一页(系统返回键)。"""
    mgr = MobileDeviceManager()
    dev = mgr.get_device(device_id)
    dev.back(delay=0.1)


def _image_signature(image_base64: str) -> list[int] | None:
    """把截图降采样为 24x24 灰度像素列表, 用于廉价视觉比对(判断页面是否还在动)。"""
    import base64 as _b64
    import io

    try:
        from PIL import Image

        raw = _b64.b64decode(image_base64)
        img = Image.open(io.BytesIO(raw)).convert("L").resize((24, 24))
        return list(img.getdata())
    except Exception:  # noqa: BLE001
        return None


def _similar(sig_a: list[int] | None, sig_b: list[int] | None, threshold: float = 4.0) -> bool:
    """两屏灰度签名的平均像素差低于阈值 → 视为几乎相同(页面未滚动, 已到底)。"""
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return False
    diff = sum(abs(a - b) for a, b in zip(sig_a, sig_b)) / len(sig_a)
    return diff < threshold


# ── Stages ─────────────────────────────────────────────

class _CollectStage(Stage):
    """打开应用+搜索, 逐屏分诊(列表全收)+ 高分条目点进详情深采。单 worker 串行独占设备。"""

    name = "collect"
    concurrency = 1
    retry = RetryPolicy(max_attempts=2, base_delay=3.0, jitter=False)

    async def _capture_save(self, ctx, keyword: str, note: str):
        """截图并落库, 返回 (base64, screenshot_id, url)。"""
        st = ctx.state
        mgr = MobileDeviceManager()
        cap = await capture_ready_screen(st["device_id"], manager=mgr)
        shot = cap.screenshot
        saved = await ma_dao.save_screenshot(
            st["db"],
            image_base64=shot.base64_data,
            project_id=st["project_id"],
            task_id=st["run_task_id"],
            device_id=st["device_id"],
            source=_OBS_SOURCE,
            width=shot.width,
            height=shot.height,
            note=note,
        )
        return shot.base64_data, saved["screenshot_id"], saved["url"]

    async def _analyze_list(self, ctx, keyword: str, image_base64: str):
        """列表页分诊: 有字段用 triage(带坐标+分数), 无字段退化整屏摘要。"""
        st = ctx.state
        fields = st["extract_fields"]
        if fields:
            return await triage_screenshot(
                image_base64,
                fields=fields,
                app_name=st["app_name"],
                keyword=keyword,
                project_id=st["project_id"],
                task_id=st["run_task_id"],
            )
        return await analyze_screenshot(
            image_base64,
            fields=fields,
            app_name=st["app_name"],
            keyword=keyword,
            project_id=st["project_id"],
            task_id=st["run_task_id"],
        )

    async def _deep_dive(self, ctx, keyword: str, candidate: dict) -> None:
        """点进一条详情 → 截图 → 综合结构化 → 返回。"""
        st = ctx.state
        stop: asyncio.Event = st["stop_event"]
        device_id = st["device_id"]
        run_task_id = st["run_task_id"]
        tap_x = candidate.get("tap_x")
        tap_y = candidate.get("tap_y")
        if not isinstance(tap_x, int) or not isinstance(tap_y, int):
            return

        obs_log(
            f"点进详情深采 score={candidate.get('score')}",
            project_id=st["project_id"] or "",
            task_id=run_task_id,
            source=_OBS_SOURCE,
            level="info",
            event="collect_detail_enter",
            data={
                "keyword": keyword,
                "score": candidate.get("score"),
                "subject_match": candidate.get("subject_match"),
                "tap": [tap_x, tap_y],
                "preview_fields": candidate.get("fields"),
            },
        )
        try:
            await asyncio.to_thread(_do_tap, device_id, tap_x, tap_y)
            await asyncio.sleep(1.5)
            if stop.is_set():
                return
            detail_max_swipes = int(st.get("detail_max_swipes", 8))
            shots_b64: list[str] = []
            shot_ids: list[str] = []
            shot_urls: list[str] = []
            source_url: str | None = None
            b64, sid, url = await self._capture_save(
                ctx, keyword, note=f"detail kw={keyword} score={candidate.get('score')}"
            )
            shots_b64.append(b64)
            shot_ids.append(sid)
            shot_urls.append(url)
            prev_sig = _image_signature(b64)

            source_link_strategy = str(st.get("source_link_strategy") or "none")
            if source_link_strategy != "none" and not stop.is_set():
                link_result = await asyncio.to_thread(
                    extract_source_link, device_id, source_link_strategy
                )
                if link_result.ok:
                    source_url = link_result.url
                    obs_log(
                        "详情页原文链接提取成功",
                        project_id=st["project_id"] or "",
                        task_id=run_task_id,
                        source=_OBS_SOURCE,
                        level="info",
                        event="collect_source_link_extracted",
                        data={
                            "keyword": keyword,
                            "strategy": source_link_strategy,
                            "url": source_url,
                            "elapsed_ms": link_result.elapsed_ms,
                        },
                    )
                else:
                    ctx.logger.warning(
                        "[collect] 原文链接提取失败 "
                        f"strategy={source_link_strategy}: {link_result.error}"
                    )
                    obs_log(
                        f"详情页原文链接提取失败: {link_result.error}",
                        project_id=st["project_id"] or "",
                        task_id=run_task_id,
                        source=_OBS_SOURCE,
                        level="warning",
                        event="collect_source_link_error",
                        data={
                            "keyword": keyword,
                            "strategy": source_link_strategy,
                            "error": link_result.error,
                            "elapsed_ms": link_result.elapsed_ms,
                        },
                    )

            # 详情页滑动到底: 逐屏截图, 需连续两屏几乎一致才判定到底(避免单帧误判提前退出)
            reached_bottom = False
            swipes = 0
            static_streak = 0
            for s in range(detail_max_swipes):
                if stop.is_set():
                    break
                try:
                    await asyncio.to_thread(_do_swipe, device_id)
                    await asyncio.sleep(float(st["swipe_interval"]))
                    b64n, sidn, urln = await self._capture_save(
                        ctx, keyword, note=f"detail{s + 2} kw={keyword}"
                    )
                    swipes += 1
                    shots_b64.append(b64n)
                    shot_ids.append(sidn)
                    shot_urls.append(urln)
                    sig = _image_signature(b64n)
                    if _similar(prev_sig, sig, threshold=2.5):
                        static_streak += 1
                    else:
                        static_streak = 0
                    prev_sig = sig
                    if static_streak >= 2:
                        reached_bottom = True
                        break
                except Exception:  # noqa: BLE001
                    break
            obs_log(
                f"详情页滑动 {swipes} 次{'(到底)' if reached_bottom else '(达上限)'} 截图 {len(shots_b64)} 张",
                project_id=st["project_id"] or "",
                task_id=run_task_id,
                source=_OBS_SOURCE,
                level="info",
                event="collect_detail_scroll",
                data={
                    "keyword": keyword,
                    "swipes": swipes,
                    "shots": len(shots_b64),
                    "reached_bottom": reached_bottom,
                },
            )
            rec = await analyze_detail(
                shots_b64,
                fields=st["extract_fields"],
                app_name=st["app_name"],
                keyword=keyword,
                project_id=st["project_id"],
                task_id=run_task_id,
            )
            if rec:
                await ctx.emit(
                    "persist",
                    {
                        "fields": rec["fields"],
                        "score": rec.get("score"),
                        "subject_match": rec.get("subject_match")
                        or candidate.get("subject_match"),
                        "score_reason": rec.get("score_reason", ""),
                        "source_url": source_url or rec.get("source_url"),
                        "keyword": keyword,
                        "screenshot_id": shot_ids[0] if shot_ids else sid,
                        "screenshot_url": shot_urls[0] if shot_urls else url,
                        "screenshot_ids": shot_ids,
                        "screenshot_urls": shot_urls,
                        "detail": True,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            ctx.logger.warning(f"[collect] 详情深采失败 kw={keyword!r}: {exc}")
            obs_log(
                f"详情深采失败: {exc}",
                project_id=st["project_id"] or "",
                task_id=run_task_id,
                source=_OBS_SOURCE,
                level="warning",
                event="collect_detail_error",
                data={"keyword": keyword, "error": str(exc)},
            )
        finally:
            try:
                await asyncio.to_thread(_do_back, device_id)
                await asyncio.sleep(0.8)
            except Exception:  # noqa: BLE001
                pass

    async def handle(self, item: Item, ctx) -> None:
        st = ctx.state
        stop: asyncio.Event = st["stop_event"]
        if stop.is_set():
            return
        keyword = str(item.payload or "")
        device_id = st["device_id"]
        app_name = st["app_name"]
        project_id = st["project_id"]
        run_task_id = st["run_task_id"]
        deep_collect = bool(st.get("deep_collect"))
        detail_max_items = int(st.get("detail_max_items", 5))
        min_score_to_detail = int(st.get("min_score_to_detail", 60))
        min_subject_match = int(st.get("min_subject_match", 70))
        no_new_stop_threshold = int(st.get("no_new_stop_threshold", 2))
        task_def_id = st["task_def_id"]
        dedup_key_fields = st["dedup_key_fields"]

        goal = f"打开{app_name}并搜索{keyword}" if keyword else f"打开{app_name}"
        if st["search_hint"]:
            goal = f"{goal};{st['search_hint']}"
        nav_plan_id = f"{run_task_id}-nav-{item.item_id}"
        try:
            async for _ev in run_planned_task(
                device_id,
                goal,
                project_id=project_id,
                owner=st["owner"],
                plan_id=nav_plan_id,
                max_replans=1,
            ):
                if stop.is_set():
                    break
        except Exception as exc:  # noqa: BLE001
            ctx.logger.warning(f"[collect] 导航失败 kw={keyword!r}: {exc}")
            obs_log(
                f"采集导航失败: {exc}",
                project_id=project_id or "",
                task_id=run_task_id,
                source=_OBS_SOURCE,
                level="warning",
                event="collect_nav_error",
                data={"keyword": keyword, "goal": goal, "error": str(exc)},
            )

        swipe_times = int(st["swipe_times"])
        swipe_interval = float(st["swipe_interval"])
        shots = 0
        emitted = 0
        seen_keys: set[str] = set()
        no_new_streak = 0
        for i in range(swipe_times + 1):
            if stop.is_set():
                break
            try:
                b64, sid, url = await self._capture_save(
                    ctx, keyword, note=f"collect kw={keyword} idx={i}"
                )
                shots += 1
                records = await self._analyze_list(ctx, keyword, b64)
                # 列表全收: 每条(浅)结构化入库
                new_this_screen = 0
                for rec in records:
                    key = collect_dao.stable_record_id(
                        task_def_id, rec["fields"], dedup_key_fields
                    )
                    if key not in seen_keys:
                        new_this_screen += 1
                        seen_keys.add(key)
                    await ctx.emit(
                        "persist",
                        {
                            "fields": rec["fields"],
                            "score": rec.get("score"),
                            "subject_match": rec.get("subject_match"),
                            "score_reason": rec.get("score_reason", ""),
                            "source_url": rec.get("source_url"),
                            "keyword": keyword,
                            "screenshot_id": sid,
                            "screenshot_url": url,
                            "detail": False,
                        },
                    )
                    emitted += 1

                obs_log(
                    f"列表分诊 kw={keyword or '-'} idx={i} 候选 {len(records)} 条",
                    project_id=project_id or "",
                    task_id=run_task_id,
                    source=_OBS_SOURCE,
                    level="info",
                    event="collect_triage",
                    data={
                        "keyword": keyword,
                        "index": i,
                        "candidates": len(records),
                        "new": new_this_screen,
                        "max_score": max((r.get("score") or 0 for r in records), default=0),
                    },
                )

                # 详情选采: 主体强对应 + 高分且有坐标的前 N 条点进深采(不什么都点)
                if deep_collect and detail_max_items > 0 and not stop.is_set():
                    candidates = [
                        r
                        for r in records
                        if (r.get("subject_match") or 0) >= min_subject_match
                        and (r.get("score") or 0) >= min_score_to_detail
                        and isinstance(r.get("tap_x"), int)
                        and isinstance(r.get("tap_y"), int)
                    ]
                    candidates.sort(
                        key=lambda r: (r.get("subject_match") or 0, r.get("score") or 0),
                        reverse=True,
                    )
                    for cand in candidates[:detail_max_items]:
                        if stop.is_set():
                            break
                        await self._deep_dive(ctx, keyword, cand)

                # 到底检测: 连续若干屏无新去重键 → 判定已滑到底, 提前停止
                if new_this_screen == 0:
                    no_new_streak += 1
                else:
                    no_new_streak = 0
                if no_new_streak >= no_new_stop_threshold:
                    obs_log(
                        f"已滑到底 kw={keyword or '-'} 第{i}屏 共见 {len(seen_keys)} 条",
                        project_id=project_id or "",
                        task_id=run_task_id,
                        source=_OBS_SOURCE,
                        level="info",
                        event="collect_reached_bottom",
                        data={
                            "keyword": keyword,
                            "index": i,
                            "seen_keys": len(seen_keys),
                            "streak": no_new_streak,
                        },
                    )
                    break
            except Exception as exc:  # noqa: BLE001
                ctx.logger.warning(f"[collect] 处理失败 kw={keyword!r} idx={i}: {exc}")
                obs_log(
                    f"采集处理失败: {exc}",
                    project_id=project_id or "",
                    task_id=run_task_id,
                    source=_OBS_SOURCE,
                    level="warning",
                    event="collect_shot_error",
                    data={"keyword": keyword, "index": i, "error": str(exc)},
                )
            if i < swipe_times and not stop.is_set():
                try:
                    await asyncio.to_thread(_do_swipe, device_id)
                except Exception as exc:  # noqa: BLE001
                    ctx.logger.warning(f"[collect] 滑动失败: {exc}")
                await asyncio.sleep(swipe_interval)
        ctx.logger.info(
            f"[collect] kw={keyword or '-'} 截屏 {shots} 张, 产出 {emitted} 条候选"
        )
        obs_log(
            f"采集完成 kw={keyword or '-'} 截屏 {shots} 张 候选 {emitted} 条",
            project_id=project_id or "",
            task_id=run_task_id,
            source=_OBS_SOURCE,
            level="info",
            event="collect_captured",
            data={"keyword": keyword, "shots": shots, "emitted": emitted},
        )


class _PersistStage(Stage):
    """增量 upsert 入库, 命中通知策略则送往 notify。"""

    name = "persist"
    concurrency = 2

    async def handle(self, item: Item, ctx) -> None:
        st = ctx.state
        payload = item.payload
        counters = st["counters"]
        raw_score = payload.get("score")
        subject_match = payload.get("subject_match")
        source_url = payload.get("source_url")

        # 分级规则: 有联系方式才能给高分, 没有的一定压到低分带
        blob = record_text_blob(payload["fields"], source_url)
        contacts = extract_contacts(blob)
        score = grade_with_contacts(raw_score, bool(contacts))

        min_persist = int(st.get("min_score_to_persist", 0) or 0)
        if min_persist > 0 and (score or 0) < min_persist:
            return

        if st.get("dry_run"):
            counters["total"] += 1
            preview = st["preview"]
            if len(preview) < st.get("preview_limit", 50):
                preview.append(
                    {
                        "fields": payload["fields"],
                        "score": score,
                        "subject_match": subject_match,
                        "score_reason": payload.get("score_reason", ""),
                        "source_url": source_url,
                        "contacts_count": len(contacts),
                        "detail": bool(payload.get("detail")),
                        "keyword": payload["keyword"],
                        "screenshot_id": payload["screenshot_id"],
                        "screenshot_url": payload["screenshot_url"],
                    }
                )
            return

        shot_ids = payload.get("screenshot_ids") or [payload["screenshot_id"]]
        shot_urls = payload.get("screenshot_urls") or [payload["screenshot_url"]]
        result = await collect_dao.upsert_record(
            st["db"],
            task_def_id=st["task_def_id"],
            project_id=st["project_id"],
            fields=payload["fields"],
            dedup_key_fields=st["dedup_key_fields"],
            screenshot_ids=shot_ids,
            screenshot_urls=shot_urls,
            keyword=payload["keyword"],
            run_task_id=st["run_task_id"],
            score=score,
            subject_match=subject_match,
            source_url=source_url,
        )
        counters["total"] += 1
        if result["is_new"]:
            counters["new"] += 1
        elif result["is_changed"]:
            counters["changed"] += 1

        # 数据分类: 抽取联系方式并接入统一 findings(需归属项目)
        project_id = st["project_id"]
        if project_id and contacts:
            record = {
                "fields": payload["fields"],
                "score": score,
                "source_url": source_url,
                "record_id": result["record_id"],
                "keyword": payload["keyword"],
                "screenshot_url": payload["screenshot_url"],
            }
            findings = build_contact_findings(
                project_id=project_id,
                task_id=st["run_task_id"],
                record=record,
                contacts=contacts,
            )
            for f in findings:
                await findings_dao.upsert_contact_finding(st["db"], f)
            counters["contacts"] = counters.get("contacts", 0) + len(findings)
            obs_log(
                f"发现联系方式 {len(findings)} 条 kw={payload['keyword'] or '-'}",
                project_id=project_id,
                task_id=st["run_task_id"],
                source=_OBS_SOURCE,
                level="notice",
                event="collect_contact_found",
                data={
                    "keyword": payload["keyword"],
                    "record_id": result["record_id"],
                    "contacts": [c["label"] for c in contacts],
                },
            )

        notify_on = st["notify_on"]
        should_notify = (result["is_new"] and notify_on in ("new", "both")) or (
            result["is_changed"] and notify_on in ("changed", "both")
        )
        if should_notify:
            await ctx.emit(
                "notify",
                {
                    "record_id": result["record_id"],
                    "fields": payload["fields"],
                    "score": score,
                    "keyword": payload["keyword"],
                    "kind": "new" if result["is_new"] else "changed",
                },
            )


class _NotifyStage(Stage):
    """增量 Hook 通知(走统一通知服务)。"""

    name = "notify"
    concurrency = 2

    async def handle(self, item: Item, ctx) -> None:
        from api.services.notifications import notify_event_background

        st = ctx.state
        payload = item.payload
        kind_label = "新增" if payload["kind"] == "new" else "变更"
        summary = payload["fields"].get("summary") or ", ".join(
            f"{k}={v}" for k, v in list(payload["fields"].items())[:5]
        )
        notify_event_background(
            event="mobile_collect_incremental",
            title=f"[采集{kind_label}] {st['task_name']}",
            content=f"关键词: {payload['keyword'] or '-'}\n{summary}",
            level="notice",
            source=_OBS_SOURCE,
            project_id=st["project_id"],
            task_id=st["run_task_id"],
            context={
                "task_def_id": st["task_def_id"],
                "record_id": payload["record_id"],
                "kind": payload["kind"],
            },
        )


# ── 编排入口 ────────────────────────────────────────────

async def run_collect_task(
    db: AsyncIOMotorDatabase,
    *,
    run_task_id: str,
    project_id: str | None,
    task_def: dict[str, Any],
    dry_run: bool = False,
    preview_limit: int = 50,
) -> dict[str, Any]:
    """执行一次采集任务。返回统计 {stopped, total, new, changed}。

    dry_run=True 时为「试跑预览」:仍占用设备、导航、截屏、视觉结构化,
    但不增量入库、不发通知,而是把结构化结果收集到返回值的 preview 列表,
    用于前端预览采集效果。
    """
    task_def_id = task_def["task_def_id"]
    device_id = task_def["device_id"]
    owner = f"collect:{run_task_id}"
    stop_event = asyncio.Event()
    _running[run_task_id] = stop_event

    keywords = list(task_def.get("keywords") or []) or [""]
    counters = {"total": 0, "new": 0, "changed": 0, "contacts": 0}
    preview: list[dict[str, Any]] = []

    device_key = await asyncio.to_thread(resolve_device_key, device_id)
    pool = DevicePool.get_instance()
    try:
        pool.acquire(device_key, owner, note="mobile_collect", device_id=device_id)
    except PoolError as exc:
        _running.pop(run_task_id, None)
        raise RuntimeError(f"设备占用失败: {exc}") from exc

    state: dict[str, Any] = {
        "db": db,
        "task_def_id": task_def_id,
        "task_name": task_def.get("name", task_def_id),
        "project_id": project_id,
        "device_id": device_id,
        "app_name": task_def.get("app_name", ""),
        "search_hint": task_def.get("search_hint", ""),
        "swipe_times": task_def.get("swipe_times", 3),
        "swipe_interval": task_def.get("swipe_interval", 1.2),
        "extract_fields": [
            f if isinstance(f, ExtractField) else ExtractField(**f)
            for f in (task_def.get("extract_fields") or [])
        ],
        "dedup_key_fields": task_def.get("dedup_key_fields") or [],
        "notify_on": task_def.get("notify_on", "new"),
        "deep_collect": bool(task_def.get("deep_collect", False)),
        "source_link_strategy": str(task_def.get("source_link_strategy") or "none"),
        "detail_max_items": int(task_def.get("detail_max_items", 5) or 0),
        "detail_max_swipes": int(task_def.get("detail_max_swipes", 12) or 12),
        "min_score_to_detail": int(task_def.get("min_score_to_detail", 60) or 0),
        "min_subject_match": int(task_def.get("min_subject_match", 70) or 0),
        "min_score_to_persist": int(task_def.get("min_score_to_persist", 0) or 0),
        "no_new_stop_threshold": int(task_def.get("no_new_stop_threshold", 2) or 2),
        "run_task_id": run_task_id,
        "owner": owner,
        "stop_event": stop_event,
        "counters": counters,
        "dry_run": dry_run,
        "preview": preview,
        "preview_limit": preview_limit,
    }

    pipe = Pipeline(state=state, pipeline_id=run_task_id[:8])
    pipe.add(_CollectStage(), downstream=["persist"])
    pipe.add(_PersistStage(), downstream=["notify"])
    pipe.add(_NotifyStage())

    obs_log(
        "采集任务开始" + ("(试跑)" if dry_run else ""),
        project_id=project_id or "",
        task_id=run_task_id,
        source=_OBS_SOURCE,
        level="notice",
        event="collect_start",
        data={
            "task_def_id": task_def_id,
            "device_id": device_id,
            "keywords": keywords,
            "dry_run": dry_run,
        },
    )
    try:
        await pipe.run(seeds=[Item(payload=kw) for kw in keywords], entry="collect")
    finally:
        _running.pop(run_task_id, None)
        try:
            pool.release(device_key, owner, force=True)
        except Exception:  # noqa: BLE001
            pass

    obs_log(
        "采集任务结束" + ("(试跑)" if dry_run else ""),
        project_id=project_id or "",
        task_id=run_task_id,
        source=_OBS_SOURCE,
        level="notice",
        event="collect_done",
        data={
            "task_def_id": task_def_id,
            "stopped": stop_event.is_set(),
            "dry_run": dry_run,
            "preview_count": len(preview),
            **counters,
        },
    )
    return {"stopped": stop_event.is_set(), "preview": preview, **counters}
