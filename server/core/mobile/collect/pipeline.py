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
from core.mobile.collect.candidate_policy import CandidatePolicyRegistry
from core.mobile.collect.search_navigation import (
    SearchNavigationRegistry,
    SearchNavigationResult,
)
from api.services.source_documents import ingest_source_url

logger = get_logger("mobile_collect")

_OBS_SOURCE = "mobile_collect"
_SOURCE_DOCUMENT_INGEST_TIMEOUT_SECONDS = 600

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


async def _run_registered_search_navigation(
    device_id: str,
    *,
    strategy: str,
    app_name: str,
    app_instance: str,
    keyword: str,
) -> SearchNavigationResult | None:
    """Use a registered deterministic navigator before visual-agent fallback."""
    navigator = SearchNavigationRegistry.create(strategy)
    if navigator is None:
        return None
    return await asyncio.to_thread(
        navigator.navigate,
        device_id,
        app_name=app_name,
        app_instance=app_instance,
        keyword=keyword,
    )


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


async def _update_parent_terminal_progress(
    db: AsyncIOMotorDatabase,
    state: dict[str, Any],
    *,
    timed_out: bool,
    all_failed: bool,
    failed_keywords: int,
) -> None:
    """Publish one terminal parent status for completed, stopped, or failed mobile work."""
    parent_task_id = str(state.get("parent_task_id") or "")
    if not parent_task_id:
        return
    from api.services.task_progress import update_source_progress

    completed = int(state.get("keywords_completed") or 0)
    total = int(state.get("keyword_total") or 0)
    stopped = bool(state["stop_event"].is_set())
    partial = timed_out or stopped or completed < total
    status = "error" if all_failed else ("partial" if partial else "completed")
    if all_failed:
        message = f"公众号采集失败，已完成 {completed}/{total} 个关键词"
    elif timed_out:
        message = f"公众号达到运行时限，已完成 {completed}/{total} 个关键词"
    elif stopped:
        message = f"公众号已停止，保留 {completed}/{total} 个关键词结果"
    else:
        message = f"公众号关键词已完成 {completed}/{total}"
    await update_source_progress(
        db,
        task_id=parent_task_id,
        source="wechat",
        total=total,
        processed=completed,
        succeeded=completed,
        failed=max(0, int(failed_keywords or 0)),
        status=status,
        message=message,
    )


# ── Stages ─────────────────────────────────────────────

class _CollectStage(Stage):
    """打开应用+搜索, 逐屏分诊(列表全收)+ 高分条目点进详情深采。单 worker 串行独占设备。"""

    name = "collect"
    concurrency = 1
    retry = RetryPolicy(max_attempts=2, base_delay=3.0, jitter=False)

    async def _navigate_to_search_results(
        self,
        ctx,
        *,
        keyword: str,
        item_id: str,
        candidate_policy,
    ) -> bool:
        """Prefer a registered navigator and retain the visual agent as fallback."""
        st = ctx.state
        stop: asyncio.Event = st["stop_event"]
        device_id = st["device_id"]
        app_name = st["app_name"]
        project_id = st["project_id"]
        run_task_id = st["run_task_id"]
        direct_launch = bool(st.get("direct_launch_app"))
        navigation_result = None

        if direct_launch:
            navigation_result = await _run_registered_search_navigation(
                device_id,
                strategy=str(st.get("source_link_strategy") or "none"),
                app_name=app_name,
                app_instance=str(st.get("app_instance") or "primary"),
                keyword=keyword,
            )
            if navigation_result is not None:
                st["direct_app_ready"] = False
                obs_log(
                    (
                        "确定性搜索导航完成"
                        if navigation_result.ok
                        else f"确定性搜索导航失败: {navigation_result.error}"
                    ),
                    project_id=project_id or "",
                    task_id=run_task_id,
                    source=_OBS_SOURCE,
                    level="info" if navigation_result.ok else "warning",
                    event=(
                        "collect_nav_deterministic"
                        if navigation_result.ok
                        else "collect_nav_deterministic_fallback"
                    ),
                    data={
                        "keyword": keyword,
                        "strategy": navigation_result.strategy,
                        "elapsed_ms": navigation_result.elapsed_ms,
                        "error": navigation_result.error or "",
                        **navigation_result.metadata,
                    },
                )
        if stop.is_set():
            return False
        if navigation_result is not None and navigation_result.ok:
            return True

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
            goal = (
                f"打开{app_name}并搜索{keyword}"
                if keyword
                else f"打开{app_name}"
            )
        if st["search_hint"]:
            goal = f"{goal};{st['search_hint']}"
        navigation_hint = candidate_policy.navigation_instructions()
        if navigation_hint:
            goal = f"{goal};{navigation_hint}"

        nav_plan_id = f"{run_task_id}-nav-{item_id}"
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
            if navigated:
                return True
            if stop.is_set():
                return False
            raise RuntimeError(
                f"手机搜索导航未完成: {app_name} {keyword}".strip()
            )
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
            target = st.get("target") or {}
            policy = CandidatePolicyRegistry.resolve(
                str(st.get("source_link_strategy") or "none")
            )
            return await triage_screenshot(
                image_base64,
                fields=fields,
                app_name=st["app_name"],
                keyword=keyword,
                target_name=str(target.get("canonical_name") or ""),
                target_aliases=list(target.get("aliases") or []),
                policy_instructions=policy.analysis_instructions(
                    target_name=str(target.get("canonical_name") or keyword),
                    aliases=list(target.get("aliases") or []),
                ),
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
    ) -> bool:
        """点进一条详情并返回是否通过审核且已交给持久化阶段。"""
        st = ctx.state
        collect_target = collect_target or st.get("target")
        stop: asyncio.Event = st["stop_event"]
        device_id = st["device_id"]
        run_task_id = st["run_task_id"]
        tap_x = candidate.get("tap_x")
        tap_y = candidate.get("tap_y")
        if not isinstance(tap_x, int) or not isinstance(tap_y, int):
            return False

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
                return False
            detail_max_swipes = int(st.get("detail_max_swipes", 8))
            shots_b64: list[str] = []
            shot_ids: list[str] = []
            shot_urls: list[str] = []
            source_url: str | None = None

            async def _persist_pending_handoff(reason: str = "") -> None:
                """Keep the extracted URL retryable without scrolling the phone."""
                await ctx.emit(
                    "persist",
                    {
                        "fields": candidate.get("fields") or {},
                        "score": candidate.get("score"),
                        "subject_match": candidate.get("subject_match"),
                        "score_reason": candidate.get("score_reason") or "",
                        "source_url": source_url,
                        "source_type": "wechat_article",
                        "source_archive_status": "pending",
                        "source_archive_error": reason,
                        "target_id": str((collect_target or {}).get("target_id") or ""),
                        "target_name": str((collect_target or {}).get("canonical_name") or ""),
                        "keyword": keyword,
                        "screenshot_id": shot_ids[0] if shot_ids else "",
                        "screenshot_url": shot_urls[0] if shot_urls else "",
                        "screenshot_ids": shot_ids,
                        "screenshot_urls": shot_urls,
                        "discovery_screenshot_ids": shot_ids,
                        "discovery_screenshot_urls": shot_urls,
                        "discovery_fields": candidate.get("fields") or {},
                        "detail": True,
                    },
                )
            b64, sid, url = await self._capture_save(
                ctx, keyword, note=f"detail kw={keyword} score={candidate.get('score')}"
            )
            shots_b64.append(b64)
            shot_ids.append(sid)
            shot_urls.append(url)
            prev_sig = _image_signature(b64)

            source_link_strategy = str(st.get("source_link_strategy") or "none")
            candidate_policy = CandidatePolicyRegistry.resolve(source_link_strategy)
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

            if (
                source_link_strategy != "none"
                and not source_url
                and not candidate_policy.allow_mobile_detail_fallback
            ):
                return False

            # 已获得真实 URL 时交给来源文档浏览器池。微信文章策略无论成功或失败
            # 都立即释放手机；失败仅保留待重试 URL，不回退逐屏深采。
            if source_url and not stop.is_set():
                try:
                    source_result = await asyncio.wait_for(
                        ingest_source_url(
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
                            min_subject_match=int(st.get("min_subject_match", 70)),
                        ),
                        timeout=_SOURCE_DOCUMENT_INGEST_TIMEOUT_SECONDS,
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
                                "discovery_fields": candidate.get("fields") or {},
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
                        return True
                    if not candidate_policy.allow_mobile_detail_fallback:
                        if source_result.get("rejected"):
                            obs_log(
                                "公众号原文未通过独立相关性审核，已丢弃本次关联",
                                project_id=st["project_id"] or "",
                                task_id=run_task_id,
                                source=_OBS_SOURCE,
                                level="info",
                                event="collect_source_document_rejected",
                                data={
                                    "keyword": keyword,
                                    "url": source_result.get("source_url") or source_url,
                                    "document_id": source_result.get("document_id"),
                                    "version_id": source_result.get("version_id"),
                                    "subject_match": source_result.get("subject_match"),
                                    "article_scope": source_result.get("article_scope"),
                                    "review_decision": source_result.get(
                                        "review_decision"
                                    ),
                                    "required_subject_match": source_result.get(
                                        "required_subject_match"
                                    ),
                                    "reason": source_result.get("score_reason")
                                    or source_result.get("reason"),
                                },
                            )
                        else:
                            await _persist_pending_handoff(
                                str(source_result.get("reason") or "浏览器归档暂未完成")
                            )
                        return False
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
                    if not candidate_policy.allow_mobile_detail_fallback:
                        await _persist_pending_handoff(str(exc))
                        return False

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
                        "discovery_fields": candidate.get("fields") or {},
                        "detail": True,
                    },
                )
                return True
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
        return False

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
        detail_max_total_items = int(st.get("detail_max_total_items", 0))
        detail_review_max_items = int(
            st.get("detail_review_max_items") or detail_max_items
        )
        detail_review_max_total_items = int(
            st.get("detail_review_max_total_items")
            or detail_max_total_items
        )
        min_score_to_detail = int(st.get("min_score_to_detail", 60))
        min_subject_match = int(st.get("min_subject_match", 70))
        no_new_stop_threshold = int(st.get("no_new_stop_threshold", 2))
        task_def_id = st["task_def_id"]
        dedup_key_fields = st["dedup_key_fields"]
        candidate_policy = CandidatePolicyRegistry.resolve(
            str(st.get("source_link_strategy") or "none")
        )

        navigated = await self._navigate_to_search_results(
            ctx,
            keyword=keyword,
            item_id=item.item_id,
            candidate_policy=candidate_policy,
        )
        if not navigated:
            return

        swipe_times = int(st["swipe_times"])
        swipe_interval = float(st["swipe_interval"])
        shots = 0
        emitted = 0
        seen_keys: set[str] = set()
        detailed_keys: set[str] = st.setdefault("detailed_record_keys", set())
        details_attempted = 0
        details_accepted = 0
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
                # 普通来源保留列表候选；文章链接来源只持久化浏览器验证后的详情。
                new_this_screen = 0
                for rec in records:
                    key = collect_dao.stable_record_id(
                        task_def_id, rec["fields"], dedup_key_fields
                    )
                    if key not in seen_keys:
                        new_this_screen += 1
                        seen_keys.add(key)
                    if candidate_policy.persist_list_candidates:
                        await ctx.emit(
                            "persist",
                            {
                                "fields": rec["fields"],
                                "score": rec.get("score"),
                                "subject_match": rec.get("subject_match"),
                                "score_reason": rec.get("score_reason", ""),
                                "source_url": rec.get("source_url"),
                                "target_id": str(
                                    (collect_target or {}).get("target_id") or ""
                                ),
                                "target_name": str(
                                    (collect_target or {}).get("canonical_name") or ""
                                ),
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
                        (
                            collect_dao.stable_record_id(
                                task_def_id,
                                r["fields"],
                                dedup_key_fields,
                            ),
                            r,
                        )
                        for r in records
                        if candidate_policy.accepts_detail(
                            r,
                            min_score=min_score_to_detail,
                            min_subject_match=min_subject_match,
                        )
                    ]
                    candidates.sort(
                        key=lambda item: (
                            item[1].get("subject_match") or 0,
                            item[1].get("score") or 0,
                        ),
                        reverse=True,
                    )
                    remaining_details = max(0, detail_max_items - details_accepted)
                    if detail_max_total_items > 0:
                        remaining_details = min(
                            remaining_details,
                            max(
                                0,
                                detail_max_total_items
                                - int(st.get("details_accepted") or 0),
                            ),
                        )
                    remaining_reviews = max(
                        0,
                        detail_review_max_items - details_attempted,
                    )
                    if detail_review_max_total_items > 0:
                        remaining_reviews = min(
                            remaining_reviews,
                            max(
                                0,
                                detail_review_max_total_items
                                - int(st.get("details_attempted") or 0),
                            ),
                        )
                    for candidate_key, cand in (
                        item
                        for item in candidates
                        if item[0] not in detailed_keys
                    ):
                        if remaining_details <= 0 or remaining_reviews <= 0:
                            break
                        if stop.is_set():
                            break
                        detailed_keys.add(candidate_key)
                        details_attempted += 1
                        st["details_attempted"] = int(
                            st.get("details_attempted") or 0
                        ) + 1
                        remaining_reviews -= 1
                        accepted = await self._deep_dive(
                            ctx,
                            keyword,
                            cand,
                            collect_target,
                        )
                        if accepted:
                            details_accepted += 1
                            st["details_accepted"] = int(
                                st.get("details_accepted") or 0
                            ) + 1
                            remaining_details -= 1

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
                parent_task_id = str(st.get("parent_task_id") or "")
                if parent_task_id:
                    from api.services.task_progress import update_source_progress

                    await update_source_progress(
                        st["db"],
                        task_id=parent_task_id,
                        source="wechat",
                        total=int(st.get("keyword_total") or 0),
                        processed=int(st.get("keywords_completed") or 0),
                        status="running",
                        message=f"公众号正在处理关键词 {keyword or '-'}，第 {i + 1} 屏",
                        extra={
                            "current_keyword": keyword,
                            "screen": i + 1,
                            "details_attempted": int(st.get("details_attempted") or 0),
                            "details_accepted": int(st.get("details_accepted") or 0),
                        },
                    )
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
        st["keywords_completed"] = int(st.get("keywords_completed") or 0) + 1
        parent_task_id = str(st.get("parent_task_id") or "")
        if parent_task_id:
            from api.services.task_progress import update_source_progress

            completed = int(st["keywords_completed"])
            total_keywords = int(st.get("keyword_total") or 0)
            await update_source_progress(
                st["db"],
                task_id=parent_task_id,
                source="wechat",
                total=total_keywords,
                processed=completed,
                succeeded=completed,
                status="completed" if completed >= total_keywords else "running",
                message=f"公众号关键词已完成 {completed}/{total_keywords}",
                extra={
                    "current_keyword": keyword,
                    "details_attempted": int(st.get("details_attempted") or 0),
                    "details_accepted": int(st.get("details_accepted") or 0),
                },
            )


def _resolve_payload_contacts(
    payload: dict[str, Any],
    source_url: str | None,
) -> list[dict[str, Any]]:
    """尊重上游对联系方式的权威判定，包括明确返回空列表的情况。"""
    if "contacts" in payload:
        return [dict(item) for item in payload.get("contacts") or []]
    return extract_contacts(record_text_blob(payload["fields"], source_url))


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
        contacts = _resolve_payload_contacts(payload, source_url)
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
            discovery_fields=(payload.get("discovery_fields") or None),
        )
        counters["total"] += 1
        if result["is_new"]:
            counters["new"] += 1
        elif result["is_changed"]:
            counters["changed"] += 1

        try:
            notification_score = max(int(raw_score or 0), int(score or 0))
        except (TypeError, ValueError):
            notification_score = int(score or 0)
        notification_min_score = int(st.get("notification_min_score", 60) or 60)
        is_high_score = notification_score >= notification_min_score
        is_incremental = bool(result["is_new"] or result["is_changed"])
        if is_high_score and is_incremental:
            counters["high_score_records"] += 1
            counters["max_score"] = max(counters["max_score"], notification_score)
            if payload.get("source_document_id"):
                counters["high_score_documents"] += 1

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
        if should_notify and is_high_score:
            await ctx.emit(
                "notify",
                {
                    "record_id": result["record_id"],
                    "fields": payload["fields"],
                    "score": notification_score,
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
    extract_fields = list(task_def.get("extract_fields") or [])
    if (
        bool(task_def.get("deep_collect"))
        and str(task_def.get("source_link_strategy") or "none") != "none"
        and not extract_fields
    ):
        raise ValueError(
            "手机详情深采已启用，但 extract_fields 为空；"
            "无法生成相关性、主体匹配和点击坐标"
        )
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
        "high_score_records": 0,
        "high_score_documents": 0,
        "max_score": 0,
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
                    include_direct_children=bool(
                        task_def.get("include_direct_children", True)
                    ),
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
            for f in extract_fields
        ],
        "dedup_key_fields": task_def.get("dedup_key_fields") or [],
        "notify_on": task_def.get("notify_on", "new"),
        "deep_collect": bool(task_def.get("deep_collect", False)),
        "source_link_strategy": str(task_def.get("source_link_strategy") or "none"),
        "direct_launch_app": bool(task_def.get("direct_launch_app", False)),
        "direct_app_ready": False,
        "app_instance": str(task_def.get("app_instance") or "primary"),
        "detail_max_items": int(task_def.get("detail_max_items", 5) or 0),
        "detail_max_total_items": int(
            task_def.get("detail_max_total_items", 0) or 0
        ),
        "detail_review_max_items": int(
            task_def.get("detail_review_max_items", 0) or 0
        ),
        "detail_review_max_total_items": int(
            task_def.get("detail_review_max_total_items", 0) or 0
        ),
        "detail_max_swipes": int(task_def.get("detail_max_swipes", 12) or 12),
        "min_score_to_detail": int(task_def.get("min_score_to_detail", 60) or 0),
        "notification_min_score": max(
            60,
            int(task_def.get("min_score_to_detail", 60) or 0),
        ),
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
        "parent_task_id": str(task_def.get("parent_task_id") or ""),
        "keyword_total": len(keywords),
        "keywords_completed": 0,
        "details_attempted": 0,
        "details_accepted": 0,
        "detailed_record_keys": set(),
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
    timed_out = False
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
        runtime_limit = max(0, int(task_def.get("max_runtime_seconds") or 0))
        try:
            if runtime_limit:
                metrics = await asyncio.wait_for(
                    pipe.run(seeds=seeds, entry="collect"),
                    timeout=runtime_limit,
                )
            else:
                metrics = await pipe.run(seeds=seeds, entry="collect")
        except asyncio.TimeoutError:
            timed_out = True
            stop_event.set()
            metrics = {}
            logger.warning(
                "[collect] 运行达到总时限，保留部分结果 | run=%s timeout=%ss",
                run_task_id,
                runtime_limit,
            )
            parent_task_id = str(state.get("parent_task_id") or "")
            if parent_task_id:
                from api.services.task_progress import update_source_progress

                await update_source_progress(
                    db,
                    task_id=parent_task_id,
                    source="wechat",
                    total=len(keywords),
                    processed=int(state.get("keywords_completed") or 0),
                    status="partial",
                    message=f"公众号达到 {runtime_limit} 秒时限，已保留部分结果",
                )
        collect_metrics = metrics.get("collect")
        collect_failed = int(getattr(collect_metrics, "failed", 0) or 0)
        collect_received = int(getattr(collect_metrics, "received", 0) or 0)
        collect_succeeded = int(getattr(collect_metrics, "succeeded", 0) or 0)
        counters["failed"] = collect_failed
        all_failed = bool(collect_received and collect_succeeded == 0)
        await _update_parent_terminal_progress(
            db,
            state,
            timed_out=timed_out,
            all_failed=all_failed,
            failed_keywords=collect_failed,
        )
        if all_failed:
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
            "timed_out": timed_out,
            **counters,
        },
    )
    return {
        "stopped": stop_event.is_set(),
        "timed_out": timed_out,
        "preview": preview,
        "keywords_used": keywords,
        "keyword_resolution": keyword_resolution,
        **counters,
    }
