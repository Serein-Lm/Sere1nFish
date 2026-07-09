"""Douyin information collection tool adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from api.services.info_collection.contracts import (
    ProfileRequest,
    ProfileResult,
    SearchRequest,
    SearchResult,
    TagRequest,
    TagResult,
)
from core.logger import get_logger


logger = get_logger("api.services.info_collection.douyin_tools")


class DouyinSearchTool:
    """Search Douyin through an encapsulated crawler runtime."""

    name = "douyin_search"

    def __init__(self, *, db: Any, crawler: Any | None = None, crawler_factory: Any | None = None) -> None:
        self._db = db
        self._crawler = crawler
        self._crawler_factory = crawler_factory
        self._owns_crawler = crawler is None

    async def _get_crawler(self) -> Any:
        if self._crawler:
            return self._crawler
        if self._crawler_factory:
            self._crawler = await self._crawler_factory()
            return self._crawler
        from crawler_tools.douyin_crawler import DouyinCrawler, DouyinCrawlerConfig

        config = DouyinCrawlerConfig()
        config.cdp_headless = True
        self._crawler = DouyinCrawler(config)
        return self._crawler

    @staticmethod
    def _normalize_video(item: dict[str, Any]) -> dict[str, Any]:
        video = dict(item)
        sec_uid = video.get("sec_uid", "")
        video["user_profile_url"] = f"https://www.douyin.com/user/{sec_uid}" if sec_uid else ""
        create_time = video.get("create_time")
        if create_time:
            try:
                video["create_time_str"] = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                video["create_time_str"] = str(create_time)
        return video

    async def search(self, request: SearchRequest) -> SearchResult:
        from api.dao import douyin as douyin_dao

        crawler = await self._get_crawler()
        active_cookie = await douyin_dao.get_active_cookie(self._db)
        if not active_cookie:
            raise RuntimeError("没有激活的账号，请先导入并激活 Cookie")

        account_name = active_cookie.get("account_name")
        cookie_string = active_cookie.get("cookie_string")
        if not cookie_string:
            raise RuntimeError(f"账号 {account_name} 的 Cookie 为空")

        login_result = await crawler.login_by_cookie_string(cookie_string)
        if not login_result.success:
            await douyin_dao.set_cookie_valid(self._db, account_name, False)
            raise RuntimeError(f"登录失败: {login_result.message}")
        await douyin_dao.set_cookie_valid(self._db, account_name, True)

        search_result = await crawler.search_videos(
            keyword=request.query,
            count=request.limit,
            publish_time=int(request.options.get("publish_time", 0)),
        )
        if not search_result.success:
            raise RuntimeError(f"搜索失败: {search_result.message}")

        videos = [self._normalize_video(item) for item in search_result.items]
        if videos:
            await douyin_dao.create_search_results_batch(
                self._db,
                request.project_id,
                request.query,
                videos,
            )
        return SearchResult(
            source="douyin",
            query=request.query,
            items=videos,
            meta={
                "task_id": request.task_id,
                "publish_time": int(request.options.get("publish_time", 0)),
                "account_name": account_name,
            },
        )

    async def close(self) -> None:
        if not self._crawler or not self._owns_crawler:
            return
        try:
            await self._crawler.close()
        except Exception as exc:
            logger.warning(f"[douyin] crawler 关闭失败: {exc}")
        finally:
            self._crawler = None


class DouyinVideoTaggingTool:
    """Tag one Douyin video through the configured tagging agent."""

    name = "douyin_video_tagging"

    def __init__(self, *, pipeline_owner: Any, agent: Any) -> None:
        self._pipeline_owner = pipeline_owner
        self._agent = agent

    async def tag(self, request: TagRequest) -> TagResult:
        from langchain_core.messages import HumanMessage

        keyword = str(request.context.get("keyword") or "")
        input_text = self._pipeline_owner._build_tagging_input(keyword, [request.item])
        raw = await self._agent({"messages": [HumanMessage(content=input_text)]})
        tagged = self._pipeline_owner._parse_tagging_response(raw, [request.item])
        tagging = tagged[0] if tagged else dict(request.item)
        tagging.setdefault("tag", "uncertain")
        tagging.setdefault("tag_reason", "未获取到打标结果")
        tagging.setdefault("confidence", "low")
        tagging.setdefault("priority", 5)
        tagging["keyword"] = keyword
        return TagResult(
            source="douyin",
            kind="video",
            item_id=request.item_id,
            tagging=tagging,
            raw=raw,
            meta={"keyword": keyword, "task_id": request.task_id},
        )


class DouyinProfileTool:
    """Generate one Douyin user profile through the pipeline owner's profile runtime."""

    name = "douyin_profile"

    def __init__(self, pipeline_owner: Any) -> None:
        self._pipeline_owner = pipeline_owner

    async def generate_profile(self, request: ProfileRequest) -> ProfileResult:
        user = request.options.get("user")
        if not isinstance(user, dict):
            return ProfileResult(
                source="douyin",
                project_id=request.project_id,
                task_id=request.task_id,
                meta={"error": "missing user"},
            )

        profile = await self._pipeline_owner._generate_profile_for_user(
            project_id=request.project_id,
            keyword=request.keyword,
            user=user,
        )
        return ProfileResult(
            source="douyin",
            project_id=request.project_id,
            task_id=request.task_id,
            profiles=[profile] if profile else [],
            meta={
                "sec_uid": user.get("_id") or user.get("sec_uid"),
                "keyword": request.keyword,
            },
        )
