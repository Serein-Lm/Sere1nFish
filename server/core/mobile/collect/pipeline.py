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

from core.mobile.app_launcher import AdbAppLauncher, AppLaunchResult
from core.mobile.coordinates import resolve_swipe, resolve_tap
from core.mobile.manager import MobileDeviceManager
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
from api.services.source_documents import ingest_source_url

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


def _do_launch_app(
    device_id: str, app_name: str, app_instance: str = "primary"
) -> AppLaunchResult:
    """通过 ADB 启动并校验前台应用，统一处理系统双开选择器。"""
    mgr = MobileDeviceManager()
    adb_id = mgr.resolve_adb_device_id(device_id)
    instance = "clone" if app_instance == "clone" else "primary"
    return AdbAppLauncher().launch(adb_id, app_name, instance=instance)


async def _run_search_navigation(
    device_id: str,
    goal: str,
    *,
    project_id: str | None,
    owner: str,
    plan_id: str,
    stop_event: asyncio.Event,
    preplanned: bool = False,
) -> bool:
    """执行看屏导航；只有明确完成才允许进入后续采集。"""
    terminal_stage = ""
    terminal_message = ""
    async for nav_event in run_planned_task(
        device_id,
        goal,
        project_id=project_id,
        owner=owner,
        plan_id=plan_id,
        max_replans=1,
        preplanned_subtasks=[goal] if preplanned else None,
    ):
        stage = str(nav_event.get("stage") or "")
        if stage in {"done", "aborted", "error", "cancelled"}:
            terminal_stage = stage
            data = nav_event.get("data")
            if isinstance(data, dict):
                terminal_message = str(
                    data.get("message") or data.get("reason") or ""
                )
        if stop_event.is_set():
            return False
    if terminal_stage != "done":
        raise RuntimeError(
            terminal_message
            or f"导航未完成，终态: {terminal_stage or 'missing'}"
        )
    return True


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

    async def _deep_dive(
        self,
        ctx,
        keyword: str,
        candidate: dict,
        collect_target: dict[str, Any] | None = None,
    ) -> None:
        """点进一条详情 → 截图 → 综合结构化 → 返回。"""
        st = ctx.state
        collect_target = collect_target or st.get("target")
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

            # 已获得真实 URL 时交给来源文档浏览器池。成功后浏览器负责全文、原图、
            # 截图与结构化，手机立即返回列表；失败才继续原有手机逐屏深采。
            if source_url and not stop.is_set():
                try:
                    source_result = await ingest_source_url(
                        st["db"],
                        url=source_url,
                        project_id=st["project_id"] or "",
                        target=collect_target,
                        task_def_id=st["task_def_id"],
                        run_task_id=run_task_id,
                        keyword=keyword,
                        extract_fields=st["extract_fields"],
                        discovery_score=candidate.get("score"),
                        discovery_subject_match=candidate.get("subject_match"),
                        discovery_context={
                            "candidate_fields": candidate.get("fields") or {},
                            "tap": [tap_x, tap_y],
                        },
                        persist=not bool(st.get("dry_run")),
                    )
                    if source_result.get("ok"):
                        browser_ids = list(
                            source_result.get("browser_screenshot_ids") or []
                        )
                        browser_urls = list(
                            source_result.get("browser_screenshot_urls") or []
                        )
                        await ctx.emit(
                            "persist",
                            {
                                "fields": source_result.get("fields") or candidate.get("fields") or {},
                                "score": source_result.get("score"),
                                "subject_match": source_result.get("subject_match"),
                                "score_reason": source_result.get("score_reason") or "",
                                "source_url": source_result.get("source_url") or source_url,
                                "source_type": source_result.get("source_type") or "wechat_article",
                                "source_document_id": source_result.get("document_id") or "",
                                "source_document_version_id": source_result.get("version_id") or "",
                                "target_id": source_result.get("target_id")
                                or str((collect_target or {}).get("target_id") or ""),
                                "target_name": source_result.get("target_name")
                                or str((collect_target or {}).get("canonical_name") or ""),
                                "contacts": source_result.get("contacts") or [],
                                "keyword": keyword,
                                "screenshot_id": shot_ids[0] if shot_ids else sid,
                                "screenshot_url": shot_urls[0] if shot_urls else url,
                                "screenshot_ids": [*shot_ids, *browser_ids],
                                "screenshot_urls": [*shot_urls, *browser_urls],
                                "browser_screenshot_ids": browser_ids,
                                "browser_screenshot_urls": browser_urls,
                                "discovery_screenshot_ids": shot_ids,
                                "discovery_screenshot_urls": shot_urls,
                                "detail": True,
                            },
                        )
                        st["counters"]["documents"] = (
                            st["counters"].get("documents", 0) + 1
                        )
                        obs_log(
                            "公众号原文已由浏览器池完整读取，跳过手机详情滚动",
                            project_id=st["project_id"] or "",
                            task_id=run_task_id,
                            source=_OBS_SOURCE,
                            level="notice",
                            event="collect_source_document_ready",
                            data={
                                "keyword": keyword,
                                "document_id": source_result.get("document_id"),
                                "version_id": source_result.get("version_id"),
                                "cached": source_result.get("cached"),
                                "images": source_result.get("image_count"),
                                "screenshots": source_result.get("screenshot_count"),
                            },
                        )
                        return
                except Exception as exc:  # noqa: BLE001
                    ctx.logger.warning(
                        f"[collect] 来源文档浏览器读取失败，回退手机深采: {exc}"
                    )
                    obs_log(
                        f"来源文档读取失败，回退手机深采: {exc}",
                        project_id=st["project_id"] or "",
                        task_id=run_task_id,
                        source=_OBS_SOURCE,
                        level="warning",
                        event="collect_source_document_fallback",
                        data={"keyword": keyword, "url": source_url, "error": str(exc)},
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
                        "target_id": str((collect_target or {}).get("target_id") or ""),
                        "target_name": str((collect_target or {}).get("canonical_name") or ""),
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
        seed = item.payload if isinstance(item.payload, dict) else {"keyword": item.payload}
        keyword = str(seed.get("keyword") or "")
        collect_target = seed.get("target") or st.get("target")
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

        direct_launch = bool(st.get("direct_launch_app"))
        if direct_launch and not bool(st.get("direct_app_ready")):
            launch_result = await asyncio.to_thread(
                _do_launch_app,
                device_id,
                app_name,
                str(st.get("app_instance") or "primary"),
            )
            if not launch_result.ok:
                raise RuntimeError(
                    f"ADB 启动应用失败: {app_name}: "
                    f"{launch_result.error or '未进入前台'}"
                )
            obs_log(
                f"ADB 已启动{app_name}",
                project_id=project_id or "",
                task_id=run_task_id,
                source=_OBS_SOURCE,
                level="info",
                event="collect_app_launched",
                data={
                    "app_name": app_name,
                    "package_name": launch_result.package_name,
                    "app_instance": launch_result.selected_instance,
                    "chooser_handled": launch_result.chooser_handled,
                },
            )
            st["direct_app_ready"] = True
        if direct_launch:
            goal = (
                f"{app_name}当前已在前台；保持在{app_name}内，"
                f"根据当前页面定位搜索入口并搜索“{keyword}”"
                if keyword
                else f"{app_name}当前已在前台；确认当前页面可操作"
            )
        else:
            goal = f"打开{app_name}并搜索{keyword}" if keyword else f"打开{app_name}"
        if st["search_hint"]:
            goal = f"{goal};{st['search_hint']}"
        nav_plan_id = f"{run_task_id}-nav-{item.item_id}"
        try:
            navigated = await _run_search_navigation(
                device_id,
                goal,
                project_id=project_id,
                owner=st["owner"],
                plan_id=nav_plan_id,
                stop_event=stop,
                preplanned=direct_launch,
            )
            if not navigated:
                return
        except Exception as exc:  # noqa: BLE001
            if direct_launch:
                st["direct_app_ready"] = False
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
            raise

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
                            "target_id": str((collect_target or {}).get("target_id") or ""),
                            "target_name": str((collect_target or {}).get("canonical_name") or ""),
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
                        await self._deep_dive(ctx, keyword, cand, collect_target)

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
        contacts = list(payload.get("contacts") or extract_contacts(blob))
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
                        "source_document_id": payload.get("source_document_id") or "",
                        "source_document_version_id": payload.get("source_document_version_id") or "",
                        "target_id": payload.get("target_id") or "",
                        "target_name": payload.get("target_name") or "",
                        "browser_screenshot_urls": payload.get("browser_screenshot_urls") or [],
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
            source_document_id=str(payload.get("source_document_id") or ""),
            source_document_version_id=str(
                payload.get("source_document_version_id") or ""
            ),
            target_id=str(payload.get("target_id") or ""),
            target_name=str(payload.get("target_name") or ""),
            browser_screenshot_ids=list(
                payload.get("browser_screenshot_ids") or []
            ),
            browser_screenshot_urls=list(
                payload.get("browser_screenshot_urls") or []
            ),
            discovery_screenshot_ids=list(
                payload.get("discovery_screenshot_ids") or []
            ),
            discovery_screenshot_urls=list(
                payload.get("discovery_screenshot_urls") or []
            ),
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
                "target_id": payload.get("target_id") or "",
                "target_name": payload.get("target_name") or "",
                "source_type": payload.get("source_type") or "mobile",
                "source_document_id": payload.get("source_document_id") or "",
                "source_document_version_id": payload.get("source_document_version_id") or "",
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

    explicit_keywords = list(task_def.get("keywords") or [])
    counters = {
        "total": 0,
        "new": 0,
        "changed": 0,
        "contacts": 0,
        "documents": 0,
    }
    preview: list[dict[str, Any]] = []

    target: dict[str, Any] | None = None
    try:
        if dry_run:
            target_name = str(task_def.get("target_name") or "").strip()
            target_id = str(task_def.get("target_id") or "").strip()
            if target_id:
                from api.dao import targets as targets_dao

                target = await targets_dao.get_target(db, target_id)
            if not target and target_name:
                target = {
                    "target_id": target_id,
                    "target_type": str(task_def.get("target_type") or "company"),
                    "canonical_name": target_name,
                }
        else:
            from api.services.targets import resolve_collection_target

            target = await resolve_collection_target(
                db,
                task_def=task_def,
                project_id=project_id or "",
            )
            if target and str(task_def.get("target_id") or "") != str(
                target.get("target_id") or ""
            ):
                await collect_dao.update_task_def(
                    db,
                    task_def_id,
                    {"target_id": target.get("target_id")},
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[collect] Target 解析失败，继续执行未聚类采集: %s", exc)

    keyword_resolution: dict[str, Any] = {
        "channel": "",
        "keywords": explicit_keywords,
        "target_ids": [],
        "sources": ["task_explicit"] if explicit_keywords else [],
    }
    if bool(task_def.get("use_target_keyword_library", True)) and project_id:
        from api.services.search_terms import infer_collection_channel

        channel = infer_collection_channel(
            app_name=str(task_def.get("app_name") or ""),
            source_link_strategy=str(task_def.get("source_link_strategy") or ""),
        )
        if channel:
            try:
                from api.services.search_terms import resolve_project_target_terms

                resolved = await resolve_project_target_terms(
                    db,
                    project_id=project_id,
                    target_id=str((target or {}).get("target_id") or task_def.get("target_id") or ""),
                    target_name=str(
                        (target or {}).get("canonical_name")
                        or task_def.get("target_name")
                        or ""
                    ),
                    channel=channel,
                    explicit_keywords=explicit_keywords,
                    include_direct_children=True,
                    max_keywords=int(task_def.get("max_resolved_keywords") or 60),
                )
                keyword_resolution = resolved.as_dict()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[collect] 项目目标词解析失败，回退显式关键词: %s", exc)
    keywords = list(keyword_resolution.get("keywords") or explicit_keywords) or [""]

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
        "target": target,
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
        "direct_launch_app": bool(task_def.get("direct_launch_app", False)),
        "direct_app_ready": False,
        "app_instance": str(task_def.get("app_instance") or "primary"),
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
        "keywords_used": keywords,
        "keyword_resolution": keyword_resolution,
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
        keyword_targets = keyword_resolution.get("keyword_targets") or {}
        seeds = []
        for keyword in keywords:
            target_info = keyword_targets.get(keyword) if isinstance(keyword_targets, dict) else None
            resolved_target = target
            if isinstance(target_info, dict) and target_info.get("target_id"):
                resolved_target = {
                    "target_id": str(target_info.get("target_id") or ""),
                    "target_type": "company",
                    "canonical_name": str(target_info.get("target_name") or ""),
                }
            seeds.append(Item(payload={"keyword": keyword, "target": resolved_target}))
        metrics = await pipe.run(seeds=seeds, entry="collect")
        collect_metrics = metrics.get("collect")
        collect_failed = int(getattr(collect_metrics, "failed", 0) or 0)
        collect_received = int(getattr(collect_metrics, "received", 0) or 0)
        collect_succeeded = int(getattr(collect_metrics, "succeeded", 0) or 0)
        counters["failed"] = collect_failed
        if collect_received and collect_succeeded == 0:
            raise RuntimeError(
                f"手机采集全部失败: {collect_failed}/{collect_received} 个关键词进入失败队列"
            )
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
    return {
        "stopped": stop_event.is_set(),
        "preview": preview,
        "keywords_used": keywords,
        "keyword_resolution": keyword_resolution,
        **counters,
    }
