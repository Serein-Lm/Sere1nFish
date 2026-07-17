"""
XHS 小红书社工信息采集 - 流水线服务

自动化流程:
搜索关键词 -> 获取笔记列表 -> Agent1: 批量打标 -> 筛选可疑笔记 
-> 获取可疑笔记详情 -> Agent2: 详情打标 -> 按 user_id 聚合 
-> Agent3: 生成人物画像 -> 存储所有结果
"""
from __future__ import annotations

import asyncio
import random
import sys
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.logger import get_logger

logger = get_logger("xhs_pipeline")

# 确保 crawler_tools 可导入
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from api.dao import xhs as xhs_dao
from api.utils.json_extract import extract_json_object
from core.observability import obs_log
from core.stream import Stage, Item, RetryPolicy

_OBS_SOURCE = "xhs_pipeline"


# ════════════════════════════════════════════════════════════
# Stage 定义 (供 _stage_profile_generation 编排, 通过 core.stream)
# 三阶段流水线: screenshot → profile → copywriting
# ════════════════════════════════════════════════════════════


class _XhsScreenshotStage(Stage):
    """截图阶段. 失败有 2 次内置重试 (核心容器热切换重试)."""
    name = "screenshot"
    retry = RetryPolicy(max_attempts=2, base_delay=5.0, jitter=False)

    def __init__(
        self,
        *,
        concurrency: int,
        project_id: str,
        task_id: str,
        db: Any,
        total_users: int,
        target_id: str = "",
        target_name: str = "",
    ) -> None:
        self.project_id = project_id
        self.task_id = task_id
        self.db = db
        self.total_users = total_users
        self.target_id = target_id
        self.target_name = target_name
        super().__init__(concurrency=concurrency)

    async def handle(self, item: Item, ctx) -> None:
        from api.services.xhs_vision_tools import screenshot_user_profile
        import time as _time

        idx = item.meta.get("idx", 0)
        finding = item.payload
        uid = finding.get("xhs_user_id")
        nick = finding.get("value", finding.get("label", ""))
        nids = finding.get("xhs_note_ids", [])

        ctx.logger.info(f"[XHS-截图] [{idx+1}/{self.total_users}] 开始 | {nick} ({uid})")
        t0 = _time.time()
        prof = await xhs_dao.create_or_update_profile(
            self.db, project_id=self.project_id, task_id=self.task_id,
            user_id=uid, nickname=nick, avatar=None, note_ids=nids,
        )
        if self.target_id:
            await self.db[xhs_dao.XHS_PROFILES_COLLECTION].update_one(
                {"project_id": self.project_id, "user_id": uid},
                {
                    "$addToSet": {
                        "target_ids": self.target_id,
                        **({"target_names": self.target_name} if self.target_name else {}),
                    }
                },
            )
        ss_r = await screenshot_user_profile(
            f"https://www.xiaohongshu.com/user/profile/{uid}", self.db,
        )
        ss = ss_r.get("screenshots", [])
        err = ss_r.get("error")
        el = _time.time() - t0
        if not ss or err:
            # 失败让框架按 RetryPolicy 重试 (容器层会热切换)
            raise RuntimeError(f"截图失败 {nick}: {err or '无截图'}")
        ctx.logger.info(f"[XHS-截图] [{idx+1}/{self.total_users}] ✓ ({el:.1f}s) | {nick} | {len(ss)}张")
        await ctx.emit("profile", {"idx": idx, "finding": finding, "profile": prof, "ss_r": ss_r})


class _XhsProfileStage(Stage):
    """画像生成 (VL + Agent). RetryPolicy 处理重试, 不再手动 attempt 循环."""
    name = "profile"
    retry = RetryPolicy(max_attempts=2, base_delay=3.0, jitter=False)

    def __init__(
        self,
        *,
        concurrency: int,
        project_id: str,
        task_id: str,
        db: Any,
        keyword: str,
        pipeline_owner: "XhsPipeline",
        total_users: int,
        target_id: str = "",
        target_name: str = "",
    ) -> None:
        self.project_id = project_id
        self.task_id = task_id
        self.db = db
        self.keyword = keyword
        self.pipeline_owner = pipeline_owner
        self.total_users = total_users
        self.target_id = target_id
        self.target_name = target_name
        super().__init__(concurrency=concurrency)

    async def on_setup(self, state: dict[str, Any]) -> None:
        state.setdefault("profiles", [])
        state.setdefault("success_count", 0)
        state.setdefault("error_count", 0)
        # 复用 agent 实例
        state["_profile_agent"] = await self.pipeline_owner._get_profile_agent()

    async def handle(self, item: Item, ctx) -> None:
        from api.services.xhs_vision_tools import analyze_screenshots_with_vision_async, save_screenshots_to_files
        from api.dao import findings as findings_dao
        from langchain_core.messages import HumanMessage
        import time as _time
        import uuid as _uuid

        wid = ctx.worker_id
        bundle = item.payload
        idx, finding, prof, ss_r = bundle["idx"], bundle["finding"], bundle["profile"], bundle["ss_r"]
        uid = finding.get("xhs_user_id")
        nick = finding.get("value", finding.get("label", ""))
        nids = finding.get("xhs_note_ids", [])
        ss = ss_r.get("screenshots", [])
        av = ss_r.get("avatar_url")

        # 1) VL 分析 (失败不致命, 只 warn)
        va, sp = "", []
        if ss and not ss_r.get("error"):
            try:
                ctx.logger.info(f"[XHS-画像-w{wid}] [{idx+1}/{self.total_users}] VL | {nick}")
                t = _time.time()
                va = await analyze_screenshots_with_vision_async(ss)
                ctx.logger.info(f"[XHS-画像-w{wid}] [{idx+1}/{self.total_users}] VL 完成 ({_time.time()-t:.1f}s)")
                if uid:
                    sp = await save_screenshots_to_files(
                        ss,
                        uid,
                        project_id=self.project_id,
                    )
            except Exception as e:
                ctx.logger.warning(f"[XHS-画像-w{wid}] VL 失败: {e}")

        # 2) Agent 调用 (失败由框架重试)
        ctx.logger.info(f"[XHS-画像-w{wid}] [{idx+1}/{self.total_users}] Agent attempt={item.attempt} | {nick}")
        ta = _time.time()
        agent = ctx.state["_profile_agent"]
        inp = self.pipeline_owner._build_profile_input(
            user_id=uid, avatar_url=av, vision_analysis=va, keyword=self.keyword,
        )
        res = await agent({"messages": [HumanMessage(content=inp)]})
        tag = self.pipeline_owner._parse_agent_response(res)
        el = _time.time() - ta
        if not tag:
            raise RuntimeError(f"画像 Agent 解析失败 {nick} ({el:.1f}s)")

        ctx.logger.info(f"[XHS-画像-w{wid}] [{idx+1}/{self.total_users}] ✓ | score={tag.get('attention_score','?')}")
        await xhs_dao.update_profile_tagging(self.db, self.project_id, uid, tag)
        ud: dict[str, Any] = {}
        if sp:
            ud["screenshot_paths"] = sp
        if av:
            ud["avatar_url"] = av
        sc = tag.get("attention_score", 50)
        fd = {
            "finding_id": _uuid.uuid4().hex[:12], "project_id": self.project_id,
            "task_id": self.task_id, "source": "xhs", "type": "personal_info",
            "channel": "xhs_profile", "label": f"疑似目标员工: {nick}", "value": nick,
            "url": f"https://www.xiaohongshu.com/user/profile/{uid}",
            "xhs_user_id": uid, "xhs_note_ids": nids, "has_profile": True,
            "notes_count": len(nids), "attention_score": sc,
            "attention_reason": tag.get("profile_summary", ""),
            "context": f"小红书用户 {nick}，关联 {len(nids)} 条笔记",
            **({"target_id": self.target_id} if self.target_id else {}),
            **({"target_name": self.target_name} if self.target_name else {}),
        }
        await findings_dao.insert_finding(self.db, fd)
        ud["finding_id"] = fd["finding_id"]
        profile_update: dict[str, Any] = {"$set": ud}
        if self.target_id:
            profile_update["$addToSet"] = {
                "target_ids": self.target_id,
                **({"target_names": self.target_name} if self.target_name else {}),
            }
        await self.db[xhs_dao.XHS_PROFILES_COLLECTION].update_one(
            {"project_id": self.project_id, "user_id": uid}, profile_update,
        )
        await findings_dao.upsert_profile(
            self.db, fd["finding_id"],
            {
                "project_id": self.project_id, "user_id": uid, "nickname": nick,
                "avatar_url": av, "attention_score": sc, "notes_count": len(nids),
                "note_ids": nids,
                **{k: tag.get(k) for k in [
                    "basic_info", "identity", "personality_profile",
                    "company_identification", "attack_surface", "profile_summary",
                    "tags", "recommended_actions",
                ] if tag.get(k)},
            },
        )
        ctx.state["profiles"].append(prof)
        ctx.state["success_count"] = ctx.state.get("success_count", 0) + 1
        await ctx.emit("xhs_copywriting", {"finding": fd, "nick": nick})


class _XhsCopywritingStage(Stage):
    """XHS 用户画像 → 话术. 复用 UrlScanPipeline 的话术 agent."""
    name = "xhs_copywriting"
    retry = RetryPolicy(max_attempts=2, base_delay=2.0, jitter=True)

    def __init__(self, *, concurrency: int, project_id: str, task_id: str, db: Any, app_config: Any) -> None:
        self.project_id = project_id
        self.task_id = task_id
        self.db = db
        self.app_config = app_config
        super().__init__(concurrency=concurrency)

    async def on_setup(self, state: dict[str, Any]) -> None:
        state.setdefault("cw_count", 0)

    async def handle(self, item: Item, ctx) -> None:
        from api.services.url_scan_pipeline import UrlScanPipeline
        from api.dao import findings as findings_dao
        import time as _time

        fd, nick = item.payload["finding"], item.payload["nick"]
        fid = fd.get("finding_id", "?")
        ctx.logger.info(f"[XHS-话术-w{ctx.worker_id}] 开始 | {fid} | {nick}")
        t0 = _time.time()
        site_ctx = {
            "url": fd.get("url", ""), "domain": "xiaohongshu.com",
            "site_name": "小红书", "entity_name": nick,
            "summary": fd.get("context", ""),
        }
        cw = await UrlScanPipeline(self.db, self.app_config).generate_copywriting_for_finding(fd, site_ctx, [])
        cw["project_id"] = self.project_id
        cw["task_id"] = self.task_id
        cw["source"] = "xhs"
        await findings_dao.insert_copywriting(self.db, cw)
        ctx.state["cw_count"] = ctx.state.get("cw_count", 0) + 1
        ctx.logger.info(f"[XHS-话术-w{ctx.worker_id}] ✓ ({_time.time()-t0:.1f}s) | {fid}")


class XhsPipeline:
    """XHS 社工信息采集流水线"""
    
    def __init__(self, db: AsyncIOMotorDatabase, app_config: Any):
        self.db = db
        self.app_config = app_config
        self._crawler = None
        self._note_tagging_agent = None
        self._detail_tagging_agent = None
        self._profile_agent = None
    
    async def _get_crawler(self):
        """获取爬虫实例"""
        if self._crawler is None:
            from crawler_tools.xhs_crawler import create_crawler
            self._crawler = await create_crawler()
        return self._crawler
    
    async def _get_note_tagging_agent(self):
        """获取笔记打标 Agent"""
        if self._note_tagging_agent is None:
            from Sere1nGraph.graph.agents.factory import create_xhs_note_tagging_agent
            self._note_tagging_agent = await create_xhs_note_tagging_agent(self.app_config)
        return self._note_tagging_agent
    
    async def _get_detail_tagging_agent(self):
        """获取详情打标 Agent"""
        if self._detail_tagging_agent is None:
            from Sere1nGraph.graph.agents.factory import create_xhs_detail_tagging_agent
            self._detail_tagging_agent = await create_xhs_detail_tagging_agent(self.app_config)
        return self._detail_tagging_agent
    
    async def _get_profile_agent(self):
        """获取人物画像 Agent"""
        if self._profile_agent is None:
            from Sere1nGraph.graph.agents.factory import create_xhs_profile_agent
            self._profile_agent = await create_xhs_profile_agent(self.app_config)
        return self._profile_agent
    
    async def run_pipeline(
        self,
        task_id: str,
        project_id: str,
        keyword: str,
        max_notes: int = 100,
        attention_threshold: int = 60,
        sort_type: str = "time_descending",  # 默认按时间排序，获取最新内容
        enable_comments: bool = False,  # 评论默认关闭
        enable_images: bool = True,  # 是否下载图片
        max_comments: int = 20,  # 每篇笔记最多获取评论数
        target_id: str = "",
        target_name: str = "",
    ) -> dict[str, Any]:
        """
        运行完整流水线
        
        Args:
            task_id: 任务 ID
            project_id: 项目 ID
            keyword: 搜索关键词（格式: 目标单位 + 关键词）
            max_notes: 最大笔记数
            attention_threshold: 关注度阈值
            sort_type: 排序方式
                - time_descending: 最新优先（默认）
                - general: 综合排序
                - popularity_descending: 热度排序
            enable_comments: 是否获取评论
            enable_images: 是否下载图片
            max_comments: 每篇笔记最多获取评论数
        
        Returns:
            流水线执行结果
        """
        result = {
            "task_id": task_id,
            "notes_count": 0,
            "suspicious_count": 0,
            "profiles_count": 0,
            "comments_count": 0,
            "images_count": 0,
            "error": None,
        }
        toolset = None
        
        try:
            # 更新任务状态为运行中
            await xhs_dao.update_search_task(self.db, task_id, {"status": "running"})
            
            import time as _time
            from api.services.info_collection.xhs_stages import (
                XhsDetailStage,
                XhsSearchStage,
                XhsTaggingStage,
            )
            from api.services.info_collection.factory import InfoCollectionToolFactory
            from api.services.info_collection.streaming import make_stream_items, run_stream_pipeline, stream_stage

            t_pipeline_start = _time.time()
            logger.info(f"[XHS] ▶ 流式 Pipeline 开始 | task={task_id} keyword='{keyword}' max_notes={max_notes}")
            obs_log(
                "XHS 采集流水线开始", task_id=task_id, project_id=project_id,
                source=_OBS_SOURCE, level="notice", event="pipeline_start",
                data={"keyword": keyword, "max_notes": max_notes},
            )

            t0 = _time.time()
            toolset = await InfoCollectionToolFactory(
                db=self.db,
                app_config=self.app_config,
            ).create_xhs_toolset(self)

            search_stage = XhsSearchStage(
                concurrency=1,
                project_id=project_id,
                task_id=task_id,
                per_keyword=max_notes,
                db=self.db,
                pipeline_owner=self,
                sort_type=sort_type,
                target_id=target_id,
                target_name=target_name,
            )
            tagging_stage = XhsTaggingStage(
                concurrency=7,
                attention_threshold=attention_threshold,
                db=self.db,
                pipeline_owner=self,
            )
            detail_stage = XhsDetailStage(
                concurrency=3,
                project_id=project_id,
                db=self.db,
                pipeline_owner=self,
                enable_comments=enable_comments,
                enable_images=enable_images,
                max_comments=max_comments,
            )

            pipe = await run_stream_pipeline(
                stages=[
                    stream_stage(search_stage, downstream=["tagging"]),
                    stream_stage(tagging_stage, downstream=["detail"]),
                    stream_stage(detail_stage),
                ],
                seeds=make_stream_items([keyword], indexed=True),
                entry="search",
                state={
                    "db": self.db,
                    **toolset.state(),
                },
            )

            result["notes_count"] = int(pipe.state.get("all_notes_count", 0))
            result["suspicious_count"] = int(pipe.state.get("all_suspicious_count", 0))
            result["comments_count"] = int(pipe.state.get("comments_count", 0))
            result["images_count"] = int(pipe.state.get("images_count", 0))
            result["detail_findings_count"] = int(pipe.state.get("detail_findings_count", 0))
            logger.info(
                f"[XHS] task={task_id} 流式搜索+打标+详情完成: "
                f"notes={result['notes_count']} suspicious={result['suspicious_count']} "
                f"findings={result['detail_findings_count']} ({_time.time()-t0:.1f}s)"
            )
            obs_log(
                f"流式采集完成: {result['notes_count']} 条笔记", task_id=task_id, project_id=project_id,
                source=_OBS_SOURCE, level="info", event="search_done", phase="search",
                data={
                    "notes": result["notes_count"],
                    "suspicious": result["suspicious_count"],
                    "detail_findings": result["detail_findings_count"],
                    "elapsed_ms": round((_time.time()-t0)*1000),
                },
            )
            
            if not result["notes_count"]:
                await xhs_dao.update_search_task(self.db, task_id, {
                    "status": "completed",
                    "notes_count": 0,
                })
                logger.info(f"[XHS] ■ Pipeline 结束（无结果）| task={task_id}")
                return result
            
            if result["suspicious_count"]:
                t0 = _time.time()
                logger.info(f"[XHS] task={task_id} 阶段5开始: 生成人物画像")
                profiles = await self._stage_profile_generation(
                    task_id,
                    project_id,
                    keyword,
                    target_id=target_id,
                    target_name=target_name,
                )
                result["profiles_count"] = len(profiles)
                logger.info(f"[XHS] task={task_id} 阶段5完成: 画像={len(profiles)} ({_time.time()-t0:.1f}s)")
                obs_log(
                    f"画像生成完成: {len(profiles)} 个", task_id=task_id, project_id=project_id,
                    source=_OBS_SOURCE, level="info", event="profiles_done", phase="profile",
                    data={"profiles": len(profiles), "elapsed_ms": round((_time.time()-t0)*1000)},
                )
            
            # 更新任务状态为完成
            await xhs_dao.update_search_task(self.db, task_id, {
                "status": "completed",
                "notes_count": result["notes_count"],
                "suspicious_count": result["suspicious_count"],
                "profiles_count": result["profiles_count"],
                "comments_count": result["comments_count"],
                "images_count": result["images_count"],
            })
            
            t_total = _time.time() - t_pipeline_start
            logger.info(
                f"[XHS] ■ Pipeline 完成 | task={task_id} keyword='{keyword}' | "
                f"notes={result['notes_count']} suspicious={result['suspicious_count']} "
                f"profiles={result['profiles_count']} | 总耗时={t_total:.1f}s"
            )
            obs_log(
                f"XHS 采集流水线完成 ({t_total:.1f}s)", task_id=task_id, project_id=project_id,
                source=_OBS_SOURCE, level="notice", event="pipeline_done",
                data={
                    "notes": result["notes_count"],
                    "suspicious": result["suspicious_count"],
                    "profiles": result["profiles_count"],
                    "elapsed_ms": round(t_total * 1000),
                },
            )
            
        except Exception as e:
            result["error"] = str(e)
            obs_log(
                f"XHS 采集流水线失败: {e}", task_id=task_id, project_id=project_id,
                source=_OBS_SOURCE, level="error", event="pipeline_error",
                data={"error": str(e)},
            )
            await xhs_dao.update_search_task(self.db, task_id, {
                "status": "failed",
                "error_message": str(e),
            })
        finally:
            if toolset:
                await toolset.close()
            # 关闭爬虫
            if self._crawler:
                await self._crawler.close()
                self._crawler = None
        
        return result
    
    async def _stage_search(
        self,
        task_id: str,
        project_id: str,
        keyword: str,
        max_notes: int,
        sort_type: str = "time_descending",
    ) -> list[dict[str, Any]]:
        """阶段 1: 搜索笔记（兼容旧入口，实际由 XhsSearchTool 执行）"""
        from api.services.info_collection import SearchRequest
        from api.services.info_collection.xhs_tools import XhsSearchTool

        result = await XhsSearchTool(
            db=self.db,
            crawler_factory=self._get_crawler,
        ).search(
            SearchRequest(
                source="xhs",
                query=keyword,
                project_id=project_id,
                task_id=task_id,
                limit=max_notes,
                options={"sort_type": sort_type},
            )
        )
        return result.items
    
    async def _stage_note_tagging(self, notes: list[dict[str, Any]], keyword: str = "") -> None:
        """阶段 2: 笔记打标（兼容旧入口，内部走工具接口 + 流式并发）."""
        if not notes:
            return

        from api.services.info_collection.factory import InfoCollectionToolFactory
        from api.services.info_collection.streaming import make_stream_items, run_stream_pipeline, stream_stage
        from api.services.info_collection.xhs_stages import XhsNoteTaggingPersistStage

        note_tagging_tool = await InfoCollectionToolFactory(
            db=self.db,
            app_config=self.app_config,
        ).create_xhs_note_tagging_tool(self)
        stage = XhsNoteTaggingPersistStage(
            concurrency=min(7, max(1, len(notes))),
            db=self.db,
            keyword=keyword,
        )
        await run_stream_pipeline(
            stages=[stream_stage(stage)],
            seeds=make_stream_items(
                [{**note, "_keyword": keyword} for note in notes],
                indexed=True,
            ),
            entry="note_tagging_persist",
            state={
                "db": self.db,
                "xhs_note_tagging_tool": note_tagging_tool,
            },
        )
    
    async def _stage_detail_tagging(
        self,
        project_id: str,
        suspicious_notes: list[dict[str, Any]],
        enable_comments: bool = False,  # 评论默认关闭
        enable_images: bool = True,
        max_comments: int = 20,
        api_max_retries: int = 2,
        screenshot_concurrency: int = 1,
    ) -> dict[str, Any]:
        """
        阶段 4: 获取详情、评论、图片并打标

        策略:
        - API 优先（串行，带风控延迟），失败 2 次后标记为需截屏
        - 截屏兜底（并发，Semaphore 控制，共享 1 个 Docker 容器）
        - 最后通过流式 Stage 并发完成存储和 Agent 打标
        """
        result = {"comments_count": 0, "images_count": 0, "detail_count": 0, "detail_findings_count": 0}
        if not suspicious_notes:
            return result

        from api.services.xhs_runtime import (
            get_xhs_runtime_config,
            record_xhs_account_result,
            select_xhs_account,
            select_xhs_proxy,
            wait_for_xhs_request_slot,
        )

        runtime_config = await get_xhs_runtime_config()
        detail_account = await select_xhs_account(self.db, purpose="detail", config=runtime_config)
        proxy = await select_xhs_proxy(runtime_config)
        cookie_str = detail_account.cookie_string
        logger.info(
            f"[XHS] 详情账号池选择 | account={detail_account.account_name} "
            f"source={detail_account.source} proxy={proxy.to_dict()}"
        )

        # 初始化 V2 客户端（xhsvm.js 本地签名，不需要浏览器）
        v2_client = None
        try:
            from crawler_tools.xhs_client_v2 import XhsClientV2
            if cookie_str:
                proxy_config = runtime_config.get("proxy_pool", {}) if isinstance(runtime_config.get("proxy_pool"), dict) else {}
                v2_client = XhsClientV2(
                    cookie_str,
                    proxy_url=proxy.proxy_url,
                    request_timeout=float(proxy_config.get("request_timeout", 30.0)),
                )
                logger.info("[XHS] V2 客户端已初始化（本地签名模式）")
        except Exception as e:
            logger.warning(f"[XHS] V2 客户端初始化失败，使用 fallback: {e}")
            await record_xhs_account_result(
                self.db,
                detail_account.account_name,
                success=False,
                error=str(e),
                cooldown_seconds=300,
            )

        # ── 第一轮: API 串行获取详情 ──
        api_success: dict[str, dict] = {}
        api_failed_notes: list[dict] = []

        for idx, note in enumerate(suspicious_notes, 1):
            note_id = note.get("note_id")
            xsec_token = note.get("xsec_token", "")
            xsec_source = note.get("xsec_source", "")

            logger.info(f"[XHS] 详情获取 ({idx}/{len(suspicious_notes)}) note={note_id}")

            # 优先用 V2 客户端（本地签名，快，不 406）
            detail = None
            if v2_client:
                try:
                    await wait_for_xhs_request_slot("detail", config=runtime_config)
                    detail = await v2_client.get_note_by_id(
                        note_id=note_id, xsec_token=xsec_token, xsec_source=xsec_source or "pc_feed",
                    )
                    if detail:
                        logger.info(f"[XHS] V2 详情成功 note={note_id}")
                        await record_xhs_account_result(self.db, detail_account.account_name, success=True)
                except Exception as e:
                    logger.warning(f"[XHS] V2 详情失败 note={note_id}: {e}")
                    await record_xhs_account_result(
                        self.db,
                        detail_account.account_name,
                        success=False,
                        error=str(e),
                        cooldown_seconds=300,
                    )

            # V2 失败，fallback 到 MediaCrawler（Playwright 签名）
            if not detail:
                crawler = await self._get_crawler()
                if hasattr(crawler, "config"):
                    crawler.config.proxy_url = proxy.proxy_url
                if not getattr(crawler, "_client", None):
                    await wait_for_xhs_request_slot("detail_fallback_login", config=runtime_config)
                    login_result = await crawler.login_by_cookie_string(cookie_str)
                    if not login_result.success:
                        await record_xhs_account_result(
                            self.db,
                            detail_account.account_name,
                            success=False,
                            error=login_result.message,
                            invalidate=True,
                            cooldown_seconds=900,
                        )
                        api_failed_notes.append(note)
                        continue
                    await record_xhs_account_result(self.db, detail_account.account_name, success=True)
                for attempt in range(1, api_max_retries + 1):
                    try:
                        await wait_for_xhs_request_slot("detail_fallback", config=runtime_config)
                        detail = await crawler._client.get_note_by_id(
                            note_id=note_id, xsec_source=xsec_source, xsec_token=xsec_token,
                        )
                        if detail:
                            logger.info(f"[XHS] Fallback 详情成功 note={note_id}")
                        break
                    except Exception as e:
                        wait = random.uniform(3, 8) * attempt
                        logger.warning(f"[XHS] Fallback 失败 note={note_id} 第{attempt}次: {e}")
                        await asyncio.sleep(wait)

            if not detail:
                api_failed_notes.append(note)
                continue

            # 间隔降低（V2 签名不依赖浏览器，风控风险低）
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # 获取评论（同样优先 V2）
            comments_data = []
            comments_summary = ""
            if enable_comments:
                try:
                    await wait_for_xhs_request_slot("comments", config=runtime_config)
                    if v2_client:
                        comments = await v2_client.get_note_all_comments(
                            note_id=note_id, xsec_token=xsec_token,
                            max_count=max_comments, crawl_interval=0.5,
                        )
                    else:
                        comments = await crawler._client.get_note_all_comments(
                            note_id=note_id, xsec_token=xsec_token,
                            max_count=max_comments, crawl_interval=1.0,
                        )
                    comments_data = comments
                    result["comments_count"] += len(comments)
                    comments_summary = self._build_comments_summary(comments)
                except Exception as e:
                    logger.warning(f"[XHS] 评论获取失败 note={note_id}: {e}")

            await asyncio.sleep(random.uniform(2, 5))

            # 图片
            images_urls = []
            if enable_images:
                try:
                    for img in detail.get("image_list", []):
                        url = img.get("url_default") or img.get("url", "")
                        if url:
                            images_urls.append(url)
                    result["images_count"] += len(images_urls)
                except Exception:
                    pass

            content = detail.get("desc", "")
            api_success[note_id] = {
                "content": content,
                "comments_summary": comments_summary,
                "comments_data": comments_data,
                "images_urls": images_urls,
            }

        # ── 第二轮: 截屏兜底（并发，Semaphore(4)，共享容器）──
        screenshot_results: dict[str, str] = {}  # note_id → vision_analysis

        if api_failed_notes:
            logger.info(f"[XHS] API 失败 {len(api_failed_notes)} 条，启用截屏兜底 (并发={screenshot_concurrency})")

            from api.services.xhs_vision_tools import screenshot_note_detail
            from browser_manager.provider import get_browser_provider

            provider = get_browser_provider()
            ss_task_id = f"xhs-detail-screenshot-{id(self)}"
            cdp_endpoint = await provider.get_cdp_endpoint(task_id=ss_task_id, purpose="xhs_screenshot")

            if cdp_endpoint:
                # 获取 cookie
                cookie_string = ""
                try:
                    cookie_string = cookie_str
                except Exception:
                    pass

                sem = asyncio.Semaphore(screenshot_concurrency)

                async def _screenshot_one(note: dict):
                    nid = note.get("note_id")
                    async with sem:
                        try:
                            r = await screenshot_note_detail(
                                note_id=nid,
                                xsec_token=note.get("xsec_token", ""),
                                cookie_string=cookie_string,
                                cdp_endpoint=cdp_endpoint,
                            )
                            if r.get("screenshots"):
                                # 视觉分析
                                from api.services.xhs_vision_tools import analyze_note_screenshots_with_vision_async
                                analysis = await analyze_note_screenshots_with_vision_async(r["screenshots"])
                                screenshot_results[nid] = analysis
                                logger.info(f"[XHS] 截屏兜底成功 note={nid}")
                            else:
                                logger.error(f"[XHS] 截屏无截图 note={nid}: {r.get('error')}")
                        except Exception as e:
                            logger.error(f"[XHS] 截屏异常 note={nid}: {e}")

                await asyncio.gather(*[_screenshot_one(n) for n in api_failed_notes])

                # 释放截屏容器
                await provider.release_cdp_endpoint(task_id=ss_task_id)
            else:
                logger.error("[XHS] 无法获取截屏容器，跳过兜底")

        # ── 第三轮: 统一存储 + Agent 打标（流式并发，保留上游预取结果）──
        prefetched_items: list[dict[str, Any]] = []
        for note in suspicious_notes:
            note_id = note.get("note_id")
            if note_id in api_success:
                data = api_success[note_id]
                prefetched_items.append({
                    "note": note,
                    "content": data["content"],
                    "comments_summary": data["comments_summary"],
                    "comments_data": data["comments_data"],
                    "images_urls": data["images_urls"],
                })
                continue
            if note_id in screenshot_results:
                prefetched_items.append({
                    "note": note,
                    "content": f"[视觉分析]\n{screenshot_results[note_id]}",
                    "comments_summary": "",
                    "comments_data": [],
                    "images_urls": [],
                })
                continue
            logger.warning(f"[XHS] note={note_id} API 和截屏都失败，跳过打标")

        if prefetched_items:
            from api.services.info_collection.factory import InfoCollectionToolFactory
            from api.services.info_collection.streaming import make_stream_items, run_stream_pipeline, stream_stage
            from api.services.info_collection.xhs_stages import XhsPrefetchedDetailTaggingStage

            detail_tagging_tool = await InfoCollectionToolFactory(
                db=self.db,
                app_config=self.app_config,
            ).create_xhs_detail_tagging_tool(self)
            detail_tagging_stage = XhsPrefetchedDetailTaggingStage(
                concurrency=min(5, max(1, len(prefetched_items))),
                project_id=project_id,
                db=self.db,
            )
            pipe = await run_stream_pipeline(
                stages=[stream_stage(detail_tagging_stage)],
                seeds=make_stream_items(prefetched_items, indexed=True),
                entry="prefetched_detail_tagging",
                state={
                    "db": self.db,
                    "xhs_detail_tagging_tool": detail_tagging_tool,
                },
            )
            result["detail_count"] = int(pipe.state.get("detail_count", 0))
            result["detail_findings_count"] = int(pipe.state.get("detail_findings_count", 0))

        if v2_client:
            await v2_client.close()
        return result
    
    def _build_comments_summary(self, comments: list[dict[str, Any]]) -> str:
        """构建评论摘要（含时间）"""
        if not comments:
            return ""
        
        summaries = []
        for c in comments[:10]:  # 最多取 10 条评论
            user_info = c.get("user_info", {})
            nickname = user_info.get("nickname", "匿名")
            content = c.get("content", "")
            create_time = c.get("create_time", 0)  # 评论时间戳
            
            # 转换时间戳为可读格式
            time_str = ""
            if create_time:
                from datetime import datetime
                try:
                    dt = datetime.fromtimestamp(create_time / 1000)  # 毫秒转秒
                    time_str = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    time_str = str(create_time)
            
            summaries.append(f"[{time_str}] {nickname}: {content[:100]}")
        
        return "\n".join(summaries)
    
    async def _stage_profile_generation(
        self,
        task_id: str,
        project_id: str,
        keyword: str = "",
        screenshot_concurrency: int = 1,
        profile_concurrency: int = 3,
        target_id: str = "",
        target_name: str = "",
    ) -> list[dict[str, Any]]:
        """
        阶段 5: 流式画像 + 话术（队列 + 并发 worker）

        数据源：findings 集合（source=xhs, 有 xhs_user_id）— 按 project_id，不限 task_id。
        截图 → 画像队列 → 话术队列，三级流水线。
        """
        from api.services.info_collection.streaming import make_stream_items, run_stream_pipeline, stream_stage

        # 从 findings 按 project_id 聚合用户（score>=60，按分数降序）
        findings_query: dict[str, Any] = {
            "project_id": project_id,
            "source": "xhs",
            "xhs_user_id": {"$exists": True, "$nin": [None, ""]},
            "attention_score": {"$gte": 60},
        }
        if target_id:
            findings_query["target_id"] = target_id
        cursor = self.db["findings"].find(
            findings_query,
            {"_id": 0},
        ).sort("attention_score", -1)
        all_f = await cursor.to_list(500)
        seen = {}
        for f in all_f:
            uid = f.get("xhs_user_id")
            if uid and uid not in seen:
                seen[uid] = f
        user_findings = list(seen.values())
        if target_id and user_findings:
            await self.db[xhs_dao.XHS_PROFILES_COLLECTION].update_many(
                {
                    "project_id": project_id,
                    "user_id": {"$in": [item.get("xhs_user_id") for item in user_findings]},
                },
                {
                    "$addToSet": {
                        "target_ids": target_id,
                        **({"target_names": target_name} if target_name else {}),
                    }
                },
            )

        # 获取项目的目标公司名（作为关键词传给画像 Agent）
        if not keyword:
            try:
                project_doc = await self.db["projects"].find_one(
                    {"_id": __import__("bson").ObjectId(project_id)},
                    {"company_name": 1, "name": 1},
                )
                if project_doc:
                    keyword = project_doc.get("company_name") or project_doc.get("name", "")
                if keyword:
                    logger.info(f"[XHS-画像] 从项目获取目标公司: '{keyword}'")
            except Exception:
                pass

        # 去重：跳过已有完整画像的用户（basic_info 不为空）
        existing_profiles = await self.db[xhs_dao.XHS_PROFILES_COLLECTION].find(
            {"project_id": project_id, "basic_info": {"$ne": None}},
            {"user_id": 1},
        ).to_list(500)
        existing_uids = {p["user_id"] for p in existing_profiles}
        before_dedup = len(user_findings)
        user_findings = [f for f in user_findings if f.get("xhs_user_id") not in existing_uids]
        if before_dedup != len(user_findings):
            logger.info(f"[XHS-画像] 去重：{before_dedup} → {len(user_findings)}（跳过 {before_dedup - len(user_findings)} 个已有画像）")
        total_users = len(user_findings)

        logger.info(f"[XHS-画像] 流式启动 | project={project_id} | 用户={total_users} | 截图={screenshot_concurrency} | 画像={profile_concurrency}")

        if not total_users:
            logger.warning("[XHS-画像] 无可用用户")
            return []

        # 通过 core.stream.Pipeline 编排三阶段: screenshot → profile → xhs_copywriting
        ss_stage = _XhsScreenshotStage(
            concurrency=screenshot_concurrency,
            project_id=project_id, task_id=task_id, db=self.db,
            total_users=total_users,
            target_id=target_id,
            target_name=target_name,
        )
        prof_stage = _XhsProfileStage(
            concurrency=profile_concurrency,
            project_id=project_id, task_id=task_id, db=self.db,
            keyword=keyword, pipeline_owner=self, total_users=total_users,
            target_id=target_id,
            target_name=target_name,
        )
        cw_stage = _XhsCopywritingStage(
            concurrency=2,
            project_id=project_id, task_id=task_id, db=self.db,
            app_config=self.app_config,
        )

        pipe = await run_stream_pipeline(
            stages=[
                stream_stage(ss_stage, downstream=["profile"]),
                stream_stage(prof_stage, downstream=["xhs_copywriting"]),
                stream_stage(cw_stage),
            ],
            seeds=make_stream_items(
                user_findings,
                meta_builder=lambda _payload, idx, _total: {"idx": idx},
            ),
            entry="screenshot",
            state={"db": self.db},
        )

        profiles = pipe.state.get("profiles", [])
        success_count = pipe.state.get("success_count", 0)
        error_count = pipe.state.get("error_count", 0)
        cw_count = pipe.state.get("cw_count", 0)
        # 把 DLQ 里的失败也计入 error_count (兼容旧版日志语义)
        error_count += len(pipe.dlq.entries) if hasattr(pipe.dlq, "entries") else 0
        logger.info(
            f"[XHS-画像] 完成 | 用户={total_users} | 画像={success_count} | "
            f"话术={cw_count} | 失败={error_count}"
        )
        return profiles
    
    @staticmethod
    def _extract_publish_time_text(item: dict) -> str:
        """从搜索结果中提取发布时间文本（如"1小时前""3天前"）"""
        corner_tags = item.get("corner_tag_info", [])
        for tag in corner_tags:
            if tag.get("type") == "publish_time":
                return tag.get("text", "")
        return ""

    def _build_note_tagging_input(self, note: dict[str, Any], keyword: str = "") -> str:
        """构建笔记打标输入"""
        user = note.get("user", {})
        publish_time = note.get("publish_time_text", "")
        return f"""搜索关键词: {keyword}

请对以下小红书笔记进行社工攻击面分析:

标题: {note.get("title", "")}
简介: {note.get("desc", "")}
用户昵称: {user.get("nickname", "")}
用户ID: {user.get("user_id", "")}
点赞数: {note.get("liked_count", "0")}
类型: {note.get("note_type", "")}
发布时间: {publish_time or "未知"}"""
    
    def _build_detail_tagging_input(self, note: dict[str, Any], content: str, comments_summary: str = "") -> str:
        """构建详情打标输入（包含评论信息）"""
        base = f"""请对以下小红书笔记详情进行深度分析:

标题: {note.get("title", "")}
完整内容:
{content}

用户昵称: {note.get("user", {}).get("nickname", "")}"""
        
        if comments_summary:
            base += f"""\n\n评论信息（注意评论时间）:
{comments_summary}"""
        
        return base
    
    def _build_profile_input(
        self,
        user_id: str,
        avatar_url: str | None,
        vision_analysis: str = "",
        keyword: str = "",
    ) -> str:
        """构建画像生成输入（基于视觉分析）"""
        
        input_text = f"""请基于以下信息生成用户画像:

搜索关键词: {keyword if keyword else "无"}

## 基础信息

用户ID: {user_id}
头像链接: {avatar_url if avatar_url else "未获取"}

## 用户主页视觉分析

{vision_analysis if vision_analysis else "视觉分析未获取"}

---

请根据视觉分析结果，生成完整的人物画像 JSON。
注意：nickname、stats（关注/粉丝/互动数）等信息需要从视觉分析中提取。"""
        
        return input_text
    
    def _parse_agent_response(self, result: dict[str, Any]) -> dict[str, Any] | None:
        """解析 Agent 响应"""
        messages = result.get("messages", []) if isinstance(result, dict) else []
        
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip():
                try:
                    return extract_json_object(content.strip())
                except Exception:
                    continue
        
        return None


async def run_xhs_pipeline(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    task_id: str,
    project_id: str,
    keyword: str,
    max_notes: int = 100,
    attention_threshold: int = 60,
    sort_type: str = "time_descending",
    enable_comments: bool = False,
    enable_images: bool = True,
    max_comments: int = 20,
    target_id: str = "",
    target_name: str = "",
) -> dict[str, Any]:
    """运行 XHS 流水线的便捷函数"""
    pipeline = XhsPipeline(db, app_config)
    return await pipeline.run_pipeline(
        task_id=task_id,
        project_id=project_id,
        keyword=keyword,
        max_notes=max_notes,
        attention_threshold=attention_threshold,
        sort_type=sort_type,
        enable_comments=enable_comments,
        enable_images=enable_images,
        max_comments=max_comments,
        target_id=target_id,
        target_name=target_name,
    )
