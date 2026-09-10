"""Search navigation runtime for one mobile collection keyword."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Awaitable

from core.mobile.app_launcher import AppLaunchResult
from core.mobile.collect.search_navigation import SearchNavigationResult


RegisteredNavigation = Callable[..., Awaitable[SearchNavigationResult | None]]
LaunchApp = Callable[[str, str, str], AppLaunchResult]
VisualNavigation = Callable[..., Awaitable[bool]]
Observer = Callable[..., Any]


class MobileSearchNavigationRunner:
    """Prefer deterministic navigation and isolate visual-agent fallback."""

    def __init__(
        self,
        *,
        registered_navigation: RegisteredNavigation,
        launch_app: LaunchApp,
        visual_navigation: VisualNavigation,
        observer: Observer,
        source: str,
    ) -> None:
        self.registered_navigation = registered_navigation
        self.launch_app = launch_app
        self.visual_navigation = visual_navigation
        self.observer = observer
        self.source = source

    async def run(
        self,
        ctx: Any,
        *,
        keyword: str,
        item_id: str,
        candidate_policy: Any,
    ) -> bool:
        state = ctx.state
        stop: asyncio.Event = state["stop_event"]
        direct_launch = bool(state.get("direct_launch_app"))
        deterministic = await self._try_deterministic(state, keyword, direct_launch)
        if stop.is_set():
            return False
        if deterministic is not None and deterministic.ok:
            return True
        await self._ensure_app_ready(state, direct_launch)
        goal = self._build_goal(state, keyword, candidate_policy, direct_launch)
        return await self._run_visual(
            ctx,
            keyword=keyword,
            item_id=item_id,
            goal=goal,
            direct_launch=direct_launch,
        )

    async def _try_deterministic(
        self,
        state: dict[str, Any],
        keyword: str,
        direct_launch: bool,
    ) -> SearchNavigationResult | None:
        if not direct_launch:
            return None
        result = await self.registered_navigation(
            state["device_id"],
            strategy=str(
                state.get("search_navigation_strategy")
                or state.get("source_link_strategy")
                or "none"
            ),
            app_name=state["app_name"],
            app_instance=str(state.get("app_instance") or "primary"),
            keyword=keyword,
        )
        if result is None:
            return None
        state["direct_app_ready"] = False
        self._observe_deterministic(state, keyword, result)
        return result

    def _observe_deterministic(
        self,
        state: dict[str, Any],
        keyword: str,
        result: SearchNavigationResult,
    ) -> None:
        self.observer(
            "确定性搜索导航完成" if result.ok else f"确定性搜索导航失败: {result.error}",
            project_id=state.get("project_id") or "",
            task_id=state["run_task_id"],
            source=self.source,
            level="info" if result.ok else "warning",
            event=(
                "collect_nav_deterministic"
                if result.ok
                else "collect_nav_deterministic_fallback"
            ),
            data={
                "keyword": keyword,
                "strategy": result.strategy,
                "elapsed_ms": result.elapsed_ms,
                "error": result.error or "",
                **result.metadata,
            },
        )

    async def _ensure_app_ready(
        self,
        state: dict[str, Any],
        direct_launch: bool,
    ) -> None:
        if not direct_launch or bool(state.get("direct_app_ready")):
            return
        result = await asyncio.to_thread(
            self.launch_app,
            state["device_id"],
            state["app_name"],
            str(state.get("app_instance") or "primary"),
        )
        if not result.ok:
            raise RuntimeError(
                f"ADB 启动应用失败: {state['app_name']}: "
                f"{result.error or '未进入前台'}"
            )
        state["direct_app_ready"] = True
        self.observer(
            f"ADB 已启动{state['app_name']}",
            project_id=state.get("project_id") or "",
            task_id=state["run_task_id"],
            source=self.source,
            level="info",
            event="collect_app_launched",
            data={
                "app_name": state["app_name"],
                "package_name": result.package_name,
                "app_instance": result.selected_instance,
                "chooser_handled": result.chooser_handled,
            },
        )

    @staticmethod
    def _build_goal(
        state: dict[str, Any],
        keyword: str,
        candidate_policy: Any,
        direct_launch: bool,
    ) -> str:
        app_name = state["app_name"]
        if direct_launch:
            goal = (
                f"{app_name}当前已在前台；保持在{app_name}内，"
                f"根据当前页面定位搜索入口并搜索“{keyword}”"
                if keyword
                else f"{app_name}当前已在前台；确认当前页面可操作"
            )
        else:
            goal = f"打开{app_name}并搜索{keyword}" if keyword else f"打开{app_name}"
        if state["search_hint"]:
            goal = f"{goal};{state['search_hint']}"
        navigation_hint = candidate_policy.navigation_instructions()
        return f"{goal};{navigation_hint}" if navigation_hint else goal

    async def _run_visual(
        self,
        ctx: Any,
        *,
        keyword: str,
        item_id: str,
        goal: str,
        direct_launch: bool,
    ) -> bool:
        state = ctx.state
        try:
            navigated = await self.visual_navigation(
                state["device_id"],
                goal,
                project_id=state["project_id"],
                owner=state["owner"],
                plan_id=f"{state['run_task_id']}-nav-{item_id}",
                stop_event=state["stop_event"],
                preplanned=direct_launch,
            )
            if navigated:
                return True
            if state["stop_event"].is_set():
                return False
            raise RuntimeError(
                f"手机搜索导航未完成: {state['app_name']} {keyword}".strip()
            )
        except Exception as error:
            if direct_launch:
                state["direct_app_ready"] = False
            ctx.logger.warning(f"[collect] 导航失败 kw={keyword!r}: {error}")
            self.observer(
                f"采集导航失败: {error}",
                project_id=state.get("project_id") or "",
                task_id=state["run_task_id"],
                source=self.source,
                level="warning",
                event="collect_nav_error",
                data={"keyword": keyword, "goal": goal, "error": str(error)},
            )
            raise
