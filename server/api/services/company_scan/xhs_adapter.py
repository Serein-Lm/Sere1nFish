"""XHS streaming collection adapter for company scans."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from api.services.info_collection.xhs_stages import (
    XhsDetailStage,
    XhsSearchStage,
    XhsTaggingStage,
)
from core.logger import get_logger


logger = get_logger("company_scan.xhs")


@dataclass(frozen=True, slots=True)
class XhsCollectionRequest:
    task_id: str
    project_id: str
    keywords: tuple[str, ...]
    max_notes: int
    attention_threshold: int
    target_id: str
    target_name: str
    search_concurrency: int

    @property
    def per_keyword(self) -> int:
        keyword_count = max(1, len(self.keywords))
        return max(1, min(40, (self.max_notes + keyword_count - 1) // keyword_count))


class XhsCollectionAdapter:
    """Own XHS stream setup, tool lifecycle and terminal projection."""

    def __init__(self, owner: Any, request: XhsCollectionRequest) -> None:
        self.owner = owner
        self.request = request

    async def run(self) -> dict[str, Any]:
        from api.services.info_collection.factory import InfoCollectionToolFactory
        from api.services.xhs_pipeline import XhsPipeline

        started = time.monotonic()
        pipeline = XhsPipeline(self.owner.db, self.owner.app_config)
        toolset = await InfoCollectionToolFactory(
            db=self.owner.db,
            app_config=self.owner.app_config,
        ).create_xhs_toolset(pipeline)
        try:
            stream = await self._run_stream(pipeline, toolset)
        except BaseException:
            await toolset.close()
            raise
        notes = int(stream.state.get("all_notes_count") or 0)
        suspicious = int(stream.state.get("all_suspicious_count") or 0)
        profiles, profile_error = await self._generate_profile(toolset)
        result = self._project(stream, notes, suspicious, profiles, profile_error)
        logger.info(
            "XHS 流水线完成 | notes=%s suspicious=%s elapsed=%.1fs",
            notes,
            suspicious,
            time.monotonic() - started,
        )
        return result

    async def _run_stream(self, pipeline: Any, toolset: Any) -> Any:
        from api.services.info_collection.streaming import (
            make_stream_items,
            run_stream_pipeline,
            stream_stage,
        )
        from api.services.xhs_runtime import resolve_xhs_search_concurrency

        request = self.request
        concurrency = await resolve_xhs_search_concurrency(
            self.owner.db,
            requested=request.search_concurrency,
            workload_size=len(request.keywords),
        )
        logger.info(
            "XHS 流式流水线启动 | keywords=%s per_keyword=%s concurrency=%s",
            len(request.keywords),
            request.per_keyword,
            concurrency,
        )
        stages = [
            stream_stage(
                XhsSearchStage(
                    concurrency=concurrency,
                    project_id=request.project_id,
                    task_id=request.task_id,
                    per_keyword=request.per_keyword,
                    db=self.owner.db,
                    pipeline_owner=pipeline,
                    target_id=request.target_id,
                    target_name=request.target_name,
                ),
                downstream=["tagging"],
            ),
            stream_stage(
                XhsTaggingStage(
                    concurrency=8,
                    attention_threshold=request.attention_threshold,
                    db=self.owner.db,
                    pipeline_owner=pipeline,
                ),
                downstream=["detail"],
            ),
            stream_stage(
                XhsDetailStage(
                    concurrency=1,
                    project_id=request.project_id,
                    db=self.owner.db,
                    pipeline_owner=pipeline,
                )
            ),
        ]
        return await run_stream_pipeline(
            stages=stages,
            seeds=make_stream_items(request.keywords, indexed=True),
            entry="search",
            state={"db": self.owner.db, **toolset.state()},
        )

    async def _generate_profile(self, toolset: Any) -> tuple[int, str]:
        from api.services.info_collection import ProfileRequest

        request = self.request
        logger.info("XHS 目标画像生成（单批次）")
        try:
            result = await toolset.profile_tool.generate_profile(
                ProfileRequest(
                    source="xhs",
                    project_id=request.project_id,
                    task_id=f"{request.task_id}_xhs_profile",
                    keyword=" / ".join(request.keywords[:4]),
                    options={
                        "target_id": request.target_id,
                        "screenshot_concurrency": 1,
                        "profile_concurrency": 2,
                    },
                )
            )
            logger.info("XHS 画像完成 | profiles=%s", result.count)
            return int(result.count or 0), ""
        except Exception as error:
            logger.error("XHS 画像失败: %s", error)
            return 0, str(error)[:1000]
        finally:
            await toolset.close()

    @staticmethod
    def _project(
        stream: Any,
        notes: int,
        suspicious: int,
        profiles: int,
        profile_error: str,
    ) -> dict[str, Any]:
        metrics = stream.metrics_summary()
        stage_failures = sum(
            int(item.get("failed") or 0) for item in metrics.values()
        )
        quality_failures = sum(
            int(stream.state.get(key) or 0)
            for key in ("tagging_errors", "detail_errors", "archive_errors")
        )
        errors = [
            *([f"流水线失败 {stage_failures} 项"] if stage_failures else []),
            *([f"内容处理不完整 {quality_failures} 项"] if quality_failures else []),
            *([f"画像生成失败: {profile_error}"] if profile_error else []),
        ]
        return {
            "kind": "xhs",
            "status": "partial" if errors else "completed",
            "notes_count": notes,
            "profiles_count": profiles,
            "stage_metrics": metrics,
            "errors": errors,
        }
