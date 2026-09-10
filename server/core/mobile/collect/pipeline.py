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
from core.mobile.collect.analysis import (
    analyze_screenshot,
    triage_screenshot,
    analyze_detail,
    verify_detail_entry,
)
from core.mobile.collect.source_links import extract_source_link

from api.dao import mobile_collect as collect_dao
from api.dao import mobile_artifacts as ma_dao
from api.dao import findings as findings_dao  # compatibility export for stage tests
from core.mobile.collect.candidate_policy import CandidatePolicyRegistry
from core.mobile.collect.candidate_history import CandidateHistory
from core.mobile.collect.candidate_time import (
    candidate_age_rejection as _candidate_age_rejection,
    candidate_publish_time as _candidate_publish_time,
)
from core.mobile.collect.search_navigation import (
    SearchNavigationRegistry,
    SearchNavigationResult,
)
from core.mobile.collect.planning import (
    finalize_keyword_resolution as _finalize_collection_keyword_resolution,
)
from core.mobile.collect.persistence_stage import (
    NotifyStage as _NotifyStage,
    PersistStage as _PersistStage,
    resolve_payload_contacts as _resolve_payload_contacts,
)
from core.mobile.collect.runtime import (
    MOBILE_RUN_REGISTRY,
    _update_parent_terminal_progress,
)
from api.services.source_documents import ingest_source_url

logger = get_logger("mobile_collect")

_OBS_SOURCE = "mobile_collect"
_SOURCE_DOCUMENT_INGEST_TIMEOUT_SECONDS = 600

# Compatibility view for callers/tests that still inspect the old module symbol.
# Ownership and lifecycle now live in MobileRunRegistry.
_running = MOBILE_RUN_REGISTRY.events


def request_stop(run_task_id: str) -> bool:
    """请求停止一个运行中的采集任务。返回是否命中。"""
    return MOBILE_RUN_REGISTRY.request_stop(run_task_id)


def is_running(run_task_id: str) -> bool:
    return MOBILE_RUN_REGISTRY.is_running(run_task_id)


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


def _do_scroll_to_top(device_id: str) -> None:
    """Normalize a remembered detail-page scroll position before verification."""
    mgr = MobileDeviceManager()
    dev = mgr.get_device(device_id)
    adb_id = mgr.resolve_adb_device_id(device_id)
    dev.press_key("move_home", delay=0.1)
    sx, sy, ex, ey = resolve_swipe(
        500, 260, 500, 860, device_id=adb_id, coord_space="normalized_1000"
    )
    for _ in range(4):
        dev.swipe(sx, sy, ex, ey, 220, delay=0.05)


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



# ── Stages ─────────────────────────────────────────────


class _CollectStage(Stage):
    """打开应用+搜索, 逐屏分诊(列表全收)+ 高分条目点进详情深采。单 worker 串行独占设备。"""

    name = "collect"
    concurrency = 1
    retry = RetryPolicy(max_attempts=2, base_delay=3.0, jitter=False)

    async def _candidate_history(
        self,
        ctx,
        collect_target: dict[str, Any] | None,
    ) -> CandidateHistory:
        st = ctx.state
        if not bool(st.get("skip_previously_collected", False)):
            return CandidateHistory()
        project_id = str(st.get("project_id") or "")
        target_id = str((collect_target or {}).get("target_id") or "")
        if not project_id or not target_id:
            return CandidateHistory()
        cache: dict[str, CandidateHistory] = st.setdefault(
            "candidate_history_cache",
            {},
        )
        cache_key = f"{project_id}:{target_id}"
        history = cache.get(cache_key)
        if history is not None:
            return history
        records = await collect_dao.list_collected_candidate_history(
            st["db"],
            project_id=project_id,
            target_id=target_id,
        )
        history = CandidateHistory.from_records(records)
        cache[cache_key] = history
        obs_log(
            "已加载手机候选历史索引",
            project_id=project_id,
            task_id=str(st.get("run_task_id") or ""),
            source=_OBS_SOURCE,
            level="info",
            event="collect_candidate_history_loaded",
            data={"target_id": target_id, "records": len(records)},
        )
        return history

    async def _navigate_to_search_results(
        self,
        ctx,
        *,
        keyword: str,
        item_id: str,
        candidate_policy,
    ) -> bool:
        """Prefer a registered navigator and retain the visual agent as fallback."""
        from core.mobile.collect.navigation_runtime import (
            MobileSearchNavigationRunner,
        )

        return await MobileSearchNavigationRunner(
            registered_navigation=_run_registered_search_navigation,
            launch_app=_do_launch_app,
            visual_navigation=_run_search_navigation,
            observer=obs_log,
            source=_OBS_SOURCE,
        ).run(
            ctx,
            keyword=keyword,
            item_id=item_id,
            candidate_policy=candidate_policy,
        )

    async def _capture_save(self, ctx, keyword: str, note: str):
        """截图并按运行模式留档，返回 (base64, screenshot_id, url)。"""
        st = ctx.state
        mgr = MobileDeviceManager()
        cap = await capture_ready_screen(st["device_id"], manager=mgr)
        shot = cap.screenshot
        if st.get("dry_run"):
            return shot.base64_data, "", ""
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

    async def _capture_for_verification(self, ctx) -> str:
        """Capture an unpersisted frame so rejected detail pages leave no artifact."""
        st = ctx.state
        cap = await capture_ready_screen(
            st["device_id"],
            manager=MobileDeviceManager(),
        )
        return cap.screenshot.base64_data

    async def _analyze_list(self, ctx, keyword: str, image_base64: str):
        """列表页分诊: 有字段用 triage(带坐标+分数), 无字段退化整屏摘要。"""
        st = ctx.state
        fields = st["extract_fields"]
        if fields:
            target = st.get("target") or {}
            subject_name = str(
                target.get("canonical_name")
                or st.get("collection_subject")
                or keyword
            )
            policy = CandidatePolicyRegistry.resolve(
                str(
                    st.get("candidate_policy")
                    or st.get("source_link_strategy")
                    or "default"
                )
            )
            return await triage_screenshot(
                image_base64,
                fields=fields,
                app_name=st["app_name"],
                keyword=keyword,
                target_name=subject_name,
                target_aliases=list(target.get("aliases") or []),
                policy_instructions=policy.analysis_instructions(
                    target_name=subject_name,
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
        from core.mobile.collect.detail_stage import MobileDetailStageRunner

        return await MobileDetailStageRunner(
            capture_save=self._capture_save,
            capture_verification=self._capture_for_verification,
            tap_action=_do_tap,
            back_action=_do_back,
            swipe_action=_do_swipe,
            scroll_top_action=_do_scroll_to_top,
            image_signature=_image_signature,
            images_similar=_similar,
            source_link_extractor=extract_source_link,
            source_ingestor=ingest_source_url,
            detail_verifier=verify_detail_entry,
            detail_analyzer=analyze_detail,
            observer=obs_log,
            ingest_timeout_seconds=_SOURCE_DOCUMENT_INGEST_TIMEOUT_SECONDS,
        ).run(ctx, keyword, candidate, collect_target)

    async def handle(self, item: Item, ctx) -> None:
        from core.mobile.collect.keyword_stage import MobileKeywordStageRunner

        await MobileKeywordStageRunner(
            collect_stage=self,
            swipe_action=_do_swipe,
            observer=obs_log,
        ).run(item, ctx)

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
    """Execute a versioned mobile collection plan through the runtime registry."""
    from core.mobile.collect.contracts import MobileCollectPlan
    from core.mobile.collect.runtime import MobileCollectRuntime
    from core.mobile.collect.stage_registry import MobileStageRegistry

    plan = MobileCollectPlan(
        db=db,
        run_task_id=run_task_id,
        project_id=project_id,
        task_def=task_def,
        dry_run=dry_run,
        preview_limit=preview_limit,
    )
    stages = MobileStageRegistry.default(
        collect=_CollectStage,
        persist=_PersistStage,
        notify=_NotifyStage,
    )
    return await MobileCollectRuntime(
        plan,
        stages=stages,
        observer=obs_log,
    ).run()
