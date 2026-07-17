"""Factory helpers for information collection tool sets.

Pipeline modules should ask this layer for tool instances instead of knowing
how each platform client, agent, or runtime helper is constructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.logger import get_logger


logger = get_logger("api.services.info_collection.factory")


@dataclass
class UrlToolset:
    scan_tool: Any
    copywriting_tool: Any
    probe_tool: Any

    def state(self) -> dict[str, Any]:
        return {
            "url_scan_tool": self.scan_tool,
            "copywriting_tool": self.copywriting_tool,
            "url_probe_tool": self.probe_tool,
        }


@dataclass
class XhsToolset:
    search_tool: Any
    detail_tool: Any
    note_tagging_tool: Any
    detail_tagging_tool: Any
    profile_tool: Any
    archive_service: Any | None = None
    v2_client: Any | None = None

    def state(self) -> dict[str, Any]:
        return {
            "v2_client": self.v2_client,
            "xhs_search_tool": self.search_tool,
            "xhs_detail_tool": self.detail_tool,
            "xhs_note_tagging_tool": self.note_tagging_tool,
            "xhs_detail_tagging_tool": self.detail_tagging_tool,
            "xhs_profile_tool": self.profile_tool,
            "xhs_archive_service": self.archive_service,
        }

    async def close(self) -> None:
        detail_close = getattr(self.detail_tool, "close", None)
        if detail_close:
            try:
                await detail_close()
            except Exception as exc:
                logger.warning(f"[xhs-stream] 详情客户端关闭失败: {exc}")
        client = self.v2_client
        if not client:
            return
        self.v2_client = None
        try:
            await client.close()
        except Exception as exc:
            logger.warning(f"[xhs-stream] V2 客户端关闭失败: {exc}")


@dataclass
class DouyinToolset:
    search_tool: Any
    video_tagging_tool: Any
    profile_tool: Any

    def state(self) -> dict[str, Any]:
        return {
            "douyin_search_tool": self.search_tool,
            "douyin_video_tagging_tool": self.video_tagging_tool,
            "douyin_profile_tool": self.profile_tool,
        }

    async def close(self) -> None:
        close = getattr(self.search_tool, "close", None)
        if close:
            await close()


class InfoCollectionToolFactory:
    """Create platform toolsets used by streaming collection pipelines."""

    def __init__(self, *, db: Any, app_config: Any) -> None:
        self.db = db
        self.app_config = app_config

    def create_copywriting_tool(self, *, response_parser: Any | None = None) -> Any:
        from api.services.info_collection.copywriting_tools import AgentCopywritingTool

        return AgentCopywritingTool(
            app_config=self.app_config,
            response_parser=response_parser,
        )

    def create_url_toolset(self, *, response_parser: Any | None = None) -> UrlToolset:
        from api.services.info_collection.url_tools import UrlProbeTool, UrlWebScanTool

        return UrlToolset(
            scan_tool=UrlWebScanTool(app_config=self.app_config, db=self.db),
            copywriting_tool=self.create_copywriting_tool(response_parser=response_parser),
            probe_tool=UrlProbeTool(),
        )

    def create_hunter_search_tool(self) -> Any:
        from api.services.info_collection.url_tools import HunterSearchProbeTool

        return HunterSearchProbeTool()

    async def create_xhs_note_tagging_tool(self, pipeline_owner: Any) -> Any:
        from api.services.info_collection.xhs_tools import XhsNoteTaggingTool

        return XhsNoteTaggingTool(
            pipeline_owner=pipeline_owner,
            agent=await pipeline_owner._get_note_tagging_agent(),
        )

    async def create_xhs_detail_tagging_tool(self, pipeline_owner: Any) -> Any:
        from api.services.info_collection.xhs_tools import XhsDetailTaggingTool

        return XhsDetailTaggingTool(
            pipeline_owner=pipeline_owner,
            agent=await pipeline_owner._get_detail_tagging_agent(),
        )

    def create_douyin_profile_tool(self, pipeline_owner: Any) -> Any:
        from api.services.info_collection.douyin_tools import DouyinProfileTool

        return DouyinProfileTool(pipeline_owner)

    async def create_douyin_video_tagging_tool(self, pipeline_owner: Any) -> Any:
        from api.services.info_collection.douyin_tools import DouyinVideoTaggingTool

        return DouyinVideoTaggingTool(
            pipeline_owner=pipeline_owner,
            agent=await pipeline_owner._get_tagging_agent(),
        )

    async def create_douyin_toolset(self, pipeline_owner: Any) -> DouyinToolset:
        from api.services.info_collection.douyin_tools import (
            DouyinSearchTool,
        )

        return DouyinToolset(
            search_tool=DouyinSearchTool(db=self.db),
            video_tagging_tool=await self.create_douyin_video_tagging_tool(pipeline_owner),
            profile_tool=self.create_douyin_profile_tool(pipeline_owner),
        )

    async def create_xhs_toolset(self, pipeline_owner: Any) -> XhsToolset:
        from api.services.info_collection.xhs_tools import (
            XhsDetailTool,
            XhsProfileTool,
            XhsSearchTool,
        )
        from api.services.xhs_archive import XhsArchiveService

        archive_service = XhsArchiveService()
        search_tool = XhsSearchTool(
            db=self.db,
            crawler_factory=getattr(pipeline_owner, "_get_crawler", None),
            archive_service=archive_service,
        )
        profile_tool = XhsProfileTool(pipeline_owner)
        note_tagging_tool = await self.create_xhs_note_tagging_tool(pipeline_owner)
        detail_tagging_tool = await self.create_xhs_detail_tagging_tool(pipeline_owner)

        return XhsToolset(
            search_tool=search_tool,
            detail_tool=XhsDetailTool(db=self.db),
            note_tagging_tool=note_tagging_tool,
            detail_tagging_tool=detail_tagging_tool,
            profile_tool=profile_tool,
            archive_service=archive_service,
        )
