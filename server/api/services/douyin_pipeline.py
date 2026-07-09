"""
抖音社工信息采集 - 流水线服务

自动化流程:
搜索关键词 -> 获取作品列表 -> Agent: 批量打标 -> 筛选潜在员工
-> 按 sec_uid 聚合 -> 生成人物画像 -> 存储所有结果
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.logger import get_logger

logger = get_logger("douyin_pipeline")

# 确保 crawler_tools 可导入
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from api.dao import douyin as douyin_dao
from api.utils.json_extract import extract_json_object
from core.observability import obs_log
from core.stream import RetryPolicy, Stage

_OBS_SOURCE = "douyin_pipeline"


class _DouyinSearchStage(Stage):
    """Search one Douyin keyword and emit videos to tagging workers."""

    name = "search"
    retry = RetryPolicy(max_attempts=1)

    def __init__(
        self,
        *,
        project_id: str,
        task_id: str,
        max_videos: int,
        publish_time: int,
    ) -> None:
        self.project_id = project_id
        self.task_id = task_id
        self.max_videos = max_videos
        self.publish_time = publish_time
        super().__init__(concurrency=1)

    async def handle(self, item: Item, ctx) -> None:
        from api.services.info_collection import SearchRequest

        keyword = item.payload
        search_tool = ctx.state.get("douyin_search_tool")
        if not search_tool:
            raise RuntimeError("douyin_search_tool 未初始化")

        result = await search_tool.search(
            SearchRequest(
                source="douyin",
                query=keyword,
                project_id=self.project_id,
                task_id=self.task_id,
                limit=self.max_videos,
                options={"publish_time": self.publish_time},
            )
        )
        ctx.state["videos_count"] = ctx.state.get("videos_count", 0) + result.count
        for video in result.items:
            video["keyword"] = keyword
            await ctx.emit("tagging", video)


class _DouyinTaggingStage(Stage):
    """Tag one Douyin video and persist the normalized result."""

    name = "tagging"
    retry = RetryPolicy(max_attempts=1)

    def __init__(self, *, project_id: str, task_id: str, keyword: str, db: Any) -> None:
        self.project_id = project_id
        self.task_id = task_id
        self.keyword = keyword
        self.db = db
        super().__init__(concurrency=5)

    async def on_setup(self, state: dict[str, Any]) -> None:
        state.setdefault("tagged_videos", [])
        state.setdefault("potential_count", 0)

    async def handle(self, item: Item, ctx) -> None:
        from api.services.info_collection import TagRequest

        video = item.payload
        aweme_id = video.get("aweme_id", "")
        tagging_tool = ctx.state.get("douyin_video_tagging_tool")
        if not tagging_tool:
            raise RuntimeError("douyin_video_tagging_tool 未初始化")

        tag_result = await tagging_tool.tag(
            TagRequest(
                source="douyin",
                kind="video",
                item_id=aweme_id,
                item=video,
                project_id=self.project_id,
                task_id=self.task_id,
                context={"keyword": video.get("keyword") or self.keyword},
            )
        )
        tagged = dict(tag_result.tagging)
        tagged.setdefault("keyword", video.get("keyword") or self.keyword)
        await douyin_dao.create_tagged_result(self.db, self.project_id, tagged)
        ctx.state["tagged_videos"].append(tagged)
        if tagged.get("tag") == "potential_employee":
            ctx.state["potential_count"] = ctx.state.get("potential_count", 0) + 1


class _DouyinProfileStage(Stage):
    """Generate one Douyin profile through a profile tool."""

    name = "profile"
    retry = RetryPolicy(max_attempts=1)

    def __init__(self, *, project_id: str, task_id: str, keyword: str) -> None:
        self.project_id = project_id
        self.task_id = task_id
        self.keyword = keyword
        super().__init__(concurrency=3)

    async def on_setup(self, state: dict[str, Any]) -> None:
        state.setdefault("profiles", [])

    async def handle(self, item: Item, ctx) -> None:
        from api.services.info_collection import ProfileRequest

        profile_tool = ctx.state.get("douyin_profile_tool")
        if not profile_tool:
            raise RuntimeError("douyin_profile_tool 未初始化")

        result = await profile_tool.generate_profile(
            ProfileRequest(
                source="douyin",
                project_id=self.project_id,
                task_id=self.task_id,
                keyword=self.keyword,
                options={"user": item.payload},
            )
        )
        ctx.state["profiles"].extend(result.profiles)


class DouyinPipeline:
    """抖音社工信息采集流水线"""
    
    def __init__(self, db: AsyncIOMotorDatabase, app_config: Any):
        self.db = db
        self.app_config = app_config
        self._crawler = None
        self._tagging_agent = None
        self._profile_agent = None
    
    async def _get_crawler(self):
        """获取爬虫实例"""
        if self._crawler is None:
            from crawler_tools.douyin_crawler import DouyinCrawler, DouyinCrawlerConfig
            config = DouyinCrawlerConfig()
            config.cdp_headless = True
            self._crawler = DouyinCrawler(config)
        return self._crawler
    
    async def _get_tagging_agent(self):
        """获取打标 Agent"""
        if self._tagging_agent is None:
            from Sere1nGraph.graph.agents.factory import create_douyin_tagging_agent
            self._tagging_agent = await create_douyin_tagging_agent(self.app_config)
        return self._tagging_agent
    
    async def _get_profile_agent(self):
        """获取人物画像 Agent"""
        if self._profile_agent is None:
            from Sere1nGraph.graph.agents.factory import create_douyin_profile_agent
            self._profile_agent = await create_douyin_profile_agent(self.app_config)
        return self._profile_agent
    
    async def run_pipeline(
        self,
        project_id: str,
        keyword: str,
        max_videos: int = 20,
        publish_time: int = 0,
        enable_profile: bool = True,
        task_id: str = "",
    ) -> dict[str, Any]:
        """
        运行完整流水线
        
        Args:
            project_id: 项目 ID
            keyword: 搜索关键词（格式: 目标单位 + 关键词，如 "b站实习"）
            max_videos: 最大视频数
            publish_time: 发布时间筛选 (0=不限, 1=一天内, 7=一周内, 180=半年内)
            enable_profile: 是否生成人物画像
        
        Returns:
            流水线执行结果
        """
        result = {
            "project_id": project_id,
            "keyword": keyword,
            "videos_count": 0,
            "potential_count": 0,
            "profiles_count": 0,
            "error": None,
        }
        
        import time as _time
        t_start = _time.time()
        toolset = None
        obs_log(
            "抖音采集流水线开始", task_id=task_id, project_id=project_id,
            source=_OBS_SOURCE, level="notice", event="pipeline_start",
            data={"keyword": keyword, "max_videos": max_videos},
        )
        try:
            from api.services.info_collection.factory import InfoCollectionToolFactory
            from api.services.info_collection.streaming import run_stream_pipeline, stream_stage

            toolset = await InfoCollectionToolFactory(
                db=self.db,
                app_config=self.app_config,
            ).create_douyin_toolset(self)

            search_stage = _DouyinSearchStage(
                project_id=project_id,
                task_id=task_id,
                max_videos=max_videos,
                publish_time=publish_time,
            )
            tagging_stage = _DouyinTaggingStage(
                project_id=project_id,
                task_id=task_id,
                keyword=keyword,
                db=self.db,
            )
            pipe = await run_stream_pipeline(
                stages=[
                    stream_stage(search_stage, downstream=["tagging"]),
                    stream_stage(tagging_stage),
                ],
                seeds=[keyword],
                entry="search",
                state={
                    "db": self.db,
                    **toolset.state(),
                },
            )

            videos = pipe.state.get("tagged_videos", [])
            result["videos_count"] = int(pipe.state.get("videos_count", 0))
            obs_log(
                f"搜索完成: {result['videos_count']} 个作品", task_id=task_id, project_id=project_id,
                source=_OBS_SOURCE, level="info", event="search_done", phase="search",
                data={
                    "videos": result["videos_count"],
                    "tagged": len(videos),
                    "potential_stream": int(pipe.state.get("potential_count", 0)),
                },
            )
            
            if not result["videos_count"]:
                obs_log(
                    "抖音采集流水线完成（无结果）", task_id=task_id, project_id=project_id,
                    source=_OBS_SOURCE, level="notice", event="pipeline_done",
                    data={"videos": 0},
                )
                return result

            potential_users = await douyin_dao.get_potential_users(self.db, project_id)
            result["potential_count"] = len(potential_users)
            
            if potential_users and enable_profile:
                profiles = await self._run_profile_stream(
                    project_id=project_id,
                    task_id=task_id,
                    keyword=keyword,
                    potential_users=potential_users,
                    tool_state=toolset.state(),
                )
                result["profiles_count"] = len(profiles)
            
            obs_log(
                f"抖音采集流水线完成 ({_time.time()-t_start:.1f}s)", task_id=task_id, project_id=project_id,
                source=_OBS_SOURCE, level="notice", event="pipeline_done",
                data={
                    "videos": result["videos_count"],
                    "potential": result["potential_count"],
                    "profiles": result["profiles_count"],
                    "elapsed_ms": round((_time.time() - t_start) * 1000),
                },
            )
        except Exception as e:
            result["error"] = str(e)
            obs_log(
                f"抖音采集流水线失败: {e}", task_id=task_id, project_id=project_id,
                source=_OBS_SOURCE, level="error", event="pipeline_error",
                data={"error": str(e)},
            )
        finally:
            if toolset:
                await toolset.close()
            # 关闭爬虫
            if self._crawler:
                await self._crawler.close()
                self._crawler = None
        
        return result

    async def _run_profile_stream(
        self,
        *,
        project_id: str,
        task_id: str,
        keyword: str,
        potential_users: list[dict[str, Any]],
        tool_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """通过 core.stream 并发生成抖音画像."""
        if not potential_users:
            return []

        from api.services.info_collection.streaming import make_stream_items, run_stream_pipeline, stream_stage

        stage = _DouyinProfileStage(
            project_id=project_id,
            task_id=task_id,
            keyword=keyword,
        )
        pipe = await run_stream_pipeline(
            stages=[stream_stage(stage)],
            seeds=make_stream_items(potential_users, indexed=True),
            entry="profile",
            state={
                "db": self.db,
                **tool_state,
            },
        )
        return pipe.state.get("profiles", [])
    
    async def _stage_search(
        self,
        project_id: str,
        keyword: str,
        max_videos: int,
        publish_time: int = 0,
    ) -> list[dict[str, Any]]:
        """阶段 1: 搜索视频（兼容旧入口，实际由 DouyinSearchTool 执行）"""
        from api.services.info_collection import SearchRequest
        from api.services.info_collection.douyin_tools import DouyinSearchTool

        result = await DouyinSearchTool(
            db=self.db,
            crawler_factory=self._get_crawler,
        ).search(
            SearchRequest(
                source="douyin",
                query=keyword,
                project_id=project_id,
                task_id="",
                limit=max_videos,
                options={"publish_time": publish_time},
            )
        )
        return result.items
    
    async def _stage_tagging(
        self,
        project_id: str,
        keyword: str,
        videos: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """阶段 2: 打标（兼容旧入口，内部走工具接口 + 流式并发）."""
        if not videos:
            return []

        from api.services.info_collection.factory import InfoCollectionToolFactory
        from api.services.info_collection.streaming import make_stream_items, run_stream_pipeline, stream_stage

        video_tagging_tool = await InfoCollectionToolFactory(
            db=self.db,
            app_config=self.app_config,
        ).create_douyin_video_tagging_tool(self)
        stage = _DouyinTaggingStage(
            project_id=project_id,
            task_id="",
            keyword=keyword,
            db=self.db,
        )
        pipe = await run_stream_pipeline(
            stages=[stream_stage(stage)],
            seeds=make_stream_items(
                [{**video, "keyword": video.get("keyword") or keyword} for video in videos],
                indexed=True,
            ),
            entry="tagging",
            state={
                "db": self.db,
                "douyin_video_tagging_tool": video_tagging_tool,
            },
        )
        return list(pipe.state.get("tagged_videos", []))
    
    async def _stage_profile_generation(
        self,
        project_id: str,
        keyword: str,
        potential_users: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """阶段 4: 生成人物画像（兼容旧入口, 内部仍走流式 Stage）."""
        from api.services.info_collection.factory import InfoCollectionToolFactory

        factory = InfoCollectionToolFactory(db=self.db, app_config=self.app_config)
        return await self._run_profile_stream(
            project_id=project_id,
            task_id="",
            keyword=keyword,
            potential_users=potential_users,
            tool_state={
                "douyin_profile_tool": factory.create_douyin_profile_tool(self),
            },
        )

    async def _generate_profile_for_user(
        self,
        *,
        project_id: str,
        keyword: str,
        user: dict[str, Any],
    ) -> dict[str, Any] | None:
        """为单个抖音用户生成画像."""
        try:
            sec_uid = user.get("_id")  # aggregation 结果中 _id 是 sec_uid
            nickname = user.get("nickname", "")
            user_profile_url = user.get("user_profile_url", "")
            avatar = user.get("avatar", "")  # 爬取的头像 URL

            if not user_profile_url and sec_uid:
                user_profile_url = f"https://www.douyin.com/user/{sec_uid}"

            # 构建爬取数据（传递给 save_profile_from_vision）
            crawled_data = {
                "nickname": nickname,
                "user_id": user.get("user_id"),
                "avatar": avatar,
                "user_profile_url": user_profile_url,
                "ip_location": user.get("ip_location"),
                "sample_title": user.get("sample_title"),
                "tag_reason": user.get("tag_reason"),
                "confidence": user.get("confidence"),
                "key_evidence": user.get("key_evidence", []),
                "company_mentioned": user.get("company_mentioned"),
                "position_mentioned": user.get("position_mentioned"),
                "priority": user.get("priority", 5),
                "aweme_count": user.get("aweme_count", 0),
            }

            # 获取用户主页的视觉分析
            vision_analysis = ""
            screenshot_paths = []

            try:
                from api.services.douyin_vision_tools import get_user_profile_vision_analysis

                vision_result = await get_user_profile_vision_analysis(
                    user_url=user_profile_url,
                    db=self.db,
                    keyword=keyword,
                    save_files=True,
                )

                if vision_result.get("success"):
                    vision_analysis = vision_result.get("vision_analysis", "")
                    screenshot_paths = vision_result.get("screenshot_paths", [])

                    # 如果视觉分析返回了解析后的 JSON，保存完整画像
                    analysis_json = vision_result.get("analysis_json")
                    if analysis_json:
                        profile = await douyin_dao.save_profile_from_vision(
                            self.db,
                            project_id=project_id,
                            sec_uid=sec_uid,
                            user_profile_url=user_profile_url,
                            avatar_url=avatar,  # 使用爬取的头像
                            analysis_result=analysis_json,
                            crawled_data=crawled_data,  # 传入爬取数据
                        )

                        # 更新截图路径
                        if screenshot_paths:
                            await self.db[douyin_dao.DOUYIN_PROFILES_COLLECTION].update_one(
                                {"project_id": project_id, "sec_uid": sec_uid},
                                {"$set": {"screenshot_paths": screenshot_paths}},
                            )

                        # 创建 finding + 回写 finding_id 到画像
                        return await self._create_finding_for_profile(
                            project_id, sec_uid, nickname, user_profile_url,
                            avatar, profile, analysis_json,
                        )

            except Exception as e:
                logger.error(f"用户 {sec_uid} 视觉分析失败: {e}")

            # 如果视觉分析失败，使用基础信息创建画像
            profile_data = {
                **crawled_data,
                "vision_analysis": vision_analysis,
                "screenshot_paths": screenshot_paths,
            }

            profile = await douyin_dao.create_or_update_profile(
                self.db, project_id, sec_uid, profile_data
            )

            # 基础画像也创建 finding
            return await self._create_finding_for_profile(
                project_id, sec_uid, nickname, user_profile_url,
                avatar, profile, crawled_data,
            )

        except Exception as e:
            logger.error(f"用户画像生成失败: {e}")
            return None

    async def _create_finding_for_profile(
        self,
        project_id: str,
        sec_uid: str,
        nickname: str,
        user_profile_url: str,
        avatar_url: str,
        profile: dict,
        analysis: dict,
    ) -> dict:
        """为抖音画像创建 finding 并回写 finding_id"""
        import uuid as _uuid
        from api.dao import findings as findings_dao

        sc = analysis.get("attention_score") or analysis.get("priority", 50)
        if isinstance(sc, str):
            try:
                sc = int(sc)
            except ValueError:
                sc = 50

        fid = _uuid.uuid4().hex[:12]
        fd = {
            "finding_id": fid,
            "project_id": project_id,
            "source": "douyin",
            "type": "personal_info",
            "channel": "douyin_profile",
            "label": f"疑似目标员工: {nickname}",
            "value": nickname,
            "url": user_profile_url,
            "douyin_sec_uid": sec_uid,
            "has_profile": True,
            "attention_score": sc,
            "attention_reason": analysis.get("profile_summary") or analysis.get("tag_reason", ""),
            "context": f"抖音用户 {nickname}",
        }
        await findings_dao.insert_finding(self.db, fd)

        # 回写 finding_id 到画像表
        await self.db[douyin_dao.DOUYIN_PROFILES_COLLECTION].update_one(
            {"project_id": project_id, "sec_uid": sec_uid},
            {"$set": {"finding_id": fid}},
        )

        # 同步写入统一 profiles 表
        await findings_dao.upsert_profile(self.db, fid, {
            "project_id": project_id,
            "sec_uid": sec_uid,
            "nickname": nickname,
            "avatar_url": avatar_url,
            "attention_score": sc,
            **{k: analysis.get(k) for k in [
                "basic_info", "identity", "personality_profile",
                "company_identification", "attack_surface",
                "profile_summary", "tags", "recommended_actions",
            ] if analysis.get(k)},
        })

        profile["finding_id"] = fid
        return profile
    
    def _build_tagging_input(self, keyword: str, videos: list[dict[str, Any]]) -> str:
        """构建打标输入"""
        import json
        
        input_data = {
            "keyword": keyword,
            "items": [
                {
                    "aweme_id": v.get("aweme_id"),
                    "title": v.get("title", "")[:200],
                    "nickname": v.get("nickname"),
                    "sec_uid": v.get("sec_uid"),
                    "user_profile_url": v.get("user_profile_url"),
                    "liked_count": v.get("liked_count"),
                    "comment_count": v.get("comment_count"),
                    "create_time_str": v.get("create_time_str"),
                }
                for v in videos
            ]
        }
        
        return f"""请根据以下搜索结果进行打标分析：

{json.dumps(input_data, ensure_ascii=False, indent=2)}

请按照 Prompt 中的输出格式，对每条作品进行打标。返回 JSON 数组格式。"""
    
    def _parse_tagging_response(
        self,
        result: dict[str, Any],
        original_videos: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """解析打标响应"""
        import re
        
        messages = result.get("messages", []) if isinstance(result, dict) else []
        response_text = ""
        
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip():
                response_text = content.strip()
                break
        
        # 尝试从响应中提取 JSON 数组
        tagging_results = []
        json_match = re.search(r'\[[\s\S]*\]', response_text)
        
        if json_match:
            try:
                import json
                tagging_results = json.loads(json_match.group())
            except Exception:
                pass
        
        # 创建 aweme_id -> tagging 映射
        tagging_map = {}
        for result in tagging_results:
            aweme_id = result.get("aweme_id")
            if aweme_id:
                tagging_map[aweme_id] = result
        
        # 合并结果
        merged_items = []
        for video in original_videos:
            aweme_id = video.get("aweme_id")
            tagging = tagging_map.get(aweme_id, {})
            
            # 合并打标信息
            merged = {**video}
            merged["tag"] = tagging.get("tag", "uncertain")
            merged["tag_reason"] = tagging.get("reason", "未获取到打标结果")
            merged["confidence"] = tagging.get("confidence", "low")
            merged["key_evidence"] = tagging.get("key_evidence", [])
            merged["company_mentioned"] = tagging.get("company_mentioned", "")
            merged["position_mentioned"] = tagging.get("position_mentioned", "")
            merged["priority"] = tagging.get("priority", 5)
            
            merged_items.append(merged)
        
        return merged_items


async def run_douyin_pipeline(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    project_id: str,
    keyword: str,
    max_videos: int = 20,
    publish_time: int = 0,
    enable_profile: bool = True,
    task_id: str = "",
) -> dict[str, Any]:
    """运行抖音流水线的便捷函数"""
    pipeline = DouyinPipeline(db, app_config)
    return await pipeline.run_pipeline(
        project_id=project_id,
        keyword=keyword,
        max_videos=max_videos,
        publish_time=publish_time,
        enable_profile=enable_profile,
        task_id=task_id,
    )
