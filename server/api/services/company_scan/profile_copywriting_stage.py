"""Profile copywriting stream stage used by company scans."""
from __future__ import annotations

from typing import Any

from api.db.collections import PROFILE_COPYWRITINGS_COLLECTION
from core.stream import Item, RetryPolicy, Stage


class ProfileCopywritingStage(Stage):
    name = "profile_copywriting"
    retry = RetryPolicy(max_attempts=2, base_delay=2.0, jitter=True)

    def __init__(
        self,
        *,
        concurrency: int,
        project_id: str,
        task_id: str,
        company_name: str,
        router_output: Any,
        db: Any,
        pipeline_owner: Any,
        target_id: str = "",
        selected_skill_ids: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.project_id = project_id
        self.task_id = task_id
        self.company_name = company_name
        self.router_output = router_output
        self.db = db
        self.pipeline_owner = pipeline_owner
        self.target_id = target_id
        self.selected_skill_ids = tuple(selected_skill_ids or ())
        super().__init__(concurrency=concurrency)

    async def on_setup(self, state: dict[str, Any]) -> None:
        import json

        from Sere1nGraph.graph.skills.schemas import FindingCopywriting

        state.setdefault("profile_copywriting_count", 0)
        state.setdefault(
            "_profile_copywriting_schema_json",
            json.dumps(
                FindingCopywriting.model_json_schema(),
                ensure_ascii=False,
                indent=2,
            ),
        )

    async def handle(self, item: Item, ctx: Any) -> None:
        from api.services.info_collection import CopywritingRequest
        from Sere1nGraph.graph.skills.schemas import FindingCopywriting

        profile = item.payload
        user_id = profile.get("user_id", "")
        tool = ctx.state.get("profile_copywriting_tool")
        if not tool:
            raise RuntimeError("profile_copywriting_tool 未初始化")
        url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
        context = self._build_context(profile, user_id, url, ctx.state)
        result = await tool.generate(
            CopywritingRequest(
                source="xhs_profile",
                project_id=self.project_id,
                task_id=self.task_id,
                target_id=user_id,
                target=profile,
                context=context,
                options={
                    "url": url,
                    "response_model": FindingCopywriting,
                    "selected_skill_ids": list(self.selected_skill_ids),
                },
            )
        )
        if not result.ok:
            ctx.logger.warning(
                f"[profile-cw-w{ctx.worker_id}] 话术生成无结果 user={user_id} "
                f"error={result.meta.get('error', '')}"
            )
            return
        for generated in result.copywritings:
            await self._persist_generated(dict(generated), user_id, url, ctx)
        ctx.logger.info(
            f"[profile-cw-w{ctx.worker_id}] 完成 user={user_id} "
            f"count={result.count} "
            f"total={ctx.state.get('profile_copywriting_count', 0)}"
        )

    def _build_context(
        self,
        profile: dict[str, Any],
        user_id: str,
        url: str,
        state: dict[str, Any],
    ) -> str:
        context = self.pipeline_owner._build_profile_copywriting_context(
            profile,
            self.company_name,
            self.router_output,
        )
        return context + (
            f"\n\n# 输出 JSON Schema\n\n```json\n"
            f"{state['_profile_copywriting_schema_json']}\n"
            f"```\n\nfinding_id 前缀: profile_{user_id or 'unknown'}\n"
            f"url: {url}"
        )

    async def _persist_generated(
        self,
        copywriting: dict[str, Any],
        user_id: str,
        url: str,
        ctx: Any,
    ) -> None:
        copywriting.setdefault("finding_id", f"profile_{user_id or 'unknown'}")
        copywriting.setdefault("url", url)
        copywriting.update(
            task_id=self.task_id,
            project_id=self.project_id,
            source="xhs_profile",
            user_id=user_id,
            status="completed",
        )
        if self.target_id:
            copywriting["target_id"] = self.target_id
        await self.db[PROFILE_COPYWRITINGS_COLLECTION].insert_one(copywriting)
        await self._link_to_finding(copywriting, user_id, ctx)
        ctx.state["profile_copywriting_count"] = int(
            ctx.state.get("profile_copywriting_count") or 0
        ) + 1

    async def _link_to_finding(
        self,
        copywriting: dict[str, Any],
        user_id: str,
        ctx: Any,
    ) -> None:
        from api.dao import findings as findings_dao

        try:
            query = {"project_id": self.project_id, "xhs_user_id": user_id}
            if self.target_id:
                query["target_id"] = self.target_id
            finding = await self.db["findings"].find_one(
                query,
                {"finding_id": 1},
            )
            if finding:
                await findings_dao.insert_copywriting(
                    self.db,
                    {**copywriting, "finding_id": finding["finding_id"]},
                )
        except Exception as error:
            ctx.logger.warning(
                f"[profile-cw-w{ctx.worker_id}] "
                f"统一话术落库失败 user={user_id}: {error}"
            )
