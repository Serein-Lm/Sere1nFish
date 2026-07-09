"""
Web Tagging 官网信息采集 - 流水线服务

自动化流程:
Hunter 查询（ICP备案名）-> URL 探活 -> 统一 URL 扫描工具并发打标 -> 存储结果
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


from core.logger import get_logger

logger = get_logger("web_tagging_pipeline")# 确保 crawler_tools 可导入
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class WebTaggingPipeline:
    """Web Tagging 官网信息采集兼容入口.

    旧入口仍然负责 Hunter 查询和观测事件；URL 打标交给
    UrlScanPipeline.scan_urls, 统一复用工具工厂、重试和流式并发扫描。
    """
    
    def __init__(self, db: AsyncIOMotorDatabase, app_config: Any):
        self.db = db
        self.app_config = app_config
    
    async def run_pipeline(
        self,
        project_id: str,
        company_name: str,
        max_urls: int = 50,
        probe_concurrency: int = 20,
        probe_timeout: float = 10.0,
        max_tagging_urls: int = 10,
        task_id: str = "",
    ) -> dict[str, Any]:
        """
        运行完整流水线
        
        Args:
            project_id: 项目 ID
            company_name: 公司名称（ICP 备案名）
            max_urls: Hunter 查询最大 URL 数
            probe_concurrency: 探活并发数
            probe_timeout: 探活超时时间
            max_tagging_urls: 最多打标的 URL 数量
        
        Returns:
            流水线执行结果
        """
        from core.observability import obs_log

        result = {
            "project_id": project_id,
            "company_name": company_name,
            "hunter_count": 0,
            "alive_count": 0,
            "tagged_count": 0,
            "findings_count": 0,
            "error": None,
        }
        obs_log(
            "Web 打标流水线开始", task_id=task_id, project_id=project_id,
            source="web_tagging_pipeline", level="notice", event="pipeline_start",
            data={"company_name": company_name},
        )
        
        try:
            # 阶段 1: Hunter 查询 + 探活
            alive_urls = await self._stage_hunter_and_probe(
                company_name, max_urls, probe_concurrency, probe_timeout
            )
            result["alive_count"] = len(alive_urls)
            
            if not alive_urls:
                obs_log(
                    "Web 打标流水线完成（无存活 URL）", task_id=task_id, project_id=project_id,
                    source="web_tagging_pipeline", level="notice", event="pipeline_done",
                    data={"alive": 0},
                )
                return result
            
            # 阶段 2: URL 扫描工具并发打标（限制数量）
            urls_to_tag = alive_urls[:max_tagging_urls]
            tagging_results = await self._stage_tagging(
                project_id,
                urls_to_tag,
                task_id=task_id,
            )
            result["tagged_count"] = len(tagging_results)
            
            # 统计 findings
            for tag_result in tagging_results:
                findings = tag_result.get("data", {}).get("findings", [])
                result["findings_count"] += len(findings)
            
            obs_log(
                "Web 打标流水线完成", task_id=task_id, project_id=project_id,
                source="web_tagging_pipeline", level="notice", event="pipeline_done",
                data={
                    "alive": result["alive_count"],
                    "tagged": result["tagged_count"],
                    "findings": result["findings_count"],
                },
            )
            
        except Exception as e:
            result["error"] = str(e)
            obs_log(
                f"Web 打标流水线失败: {e}", task_id=task_id, project_id=project_id,
                source="web_tagging_pipeline", level="error", event="pipeline_error",
                data={"error": str(e)},
            )
        
        return result
    
    async def _stage_hunter_and_probe(
        self,
        company_name: str,
        max_urls: int,
        probe_concurrency: int,
        probe_timeout: float,
    ) -> list[dict[str, Any]]:
        """阶段 1: Hunter 查询 + URL 探活（通过工具接口执行）"""
        from api.services.info_collection import SearchRequest
        from api.services.info_collection.factory import InfoCollectionToolFactory

        search_tool = InfoCollectionToolFactory(
            db=self.db,
            app_config=self.app_config,
        ).create_hunter_search_tool()
        result = await search_tool.search(
            SearchRequest(
                source="hunter",
                query=company_name,
                project_id="",
                task_id="",
                limit=max_urls,
                options={
                    "search_type": "icp",
                    "probe_concurrency": probe_concurrency,
                    "probe_timeout": probe_timeout,
                },
            )
        )
        return result.items
    
    async def _stage_tagging(
        self,
        project_id: str,
        alive_urls: list[dict[str, Any]],
        task_id: str = "",
    ) -> list[dict[str, Any]]:
        """阶段 2: URL 打标.

        这里不再直接创建 agent；统一交给 UrlScanPipeline 的 scan stage,
        由 url_scan_tool 处理 Chrome 租用、Agent 调用、解析、重试和入库。
        """
        if not alive_urls:
            return []

        from api.services.url_scan_pipeline import UrlScanPipeline

        pipeline = UrlScanPipeline(self.db, self.app_config)
        num_workers = min(6, max(1, len(alive_urls)))
        return await pipeline.scan_urls(
            project_id=project_id,
            alive_urls=alive_urls,
            task_id=task_id,
            num_workers=num_workers,
        )

    async def run_single_url(
        self,
        project_id: str,
        url: str,
    ) -> dict[str, Any]:
        """
        对单个 URL 进行打标
        
        Args:
            project_id: 项目 ID
            url: 要打标的 URL
        
        Returns:
            打标结果
        """
        results = await self._stage_tagging(
            project_id,
            [{"url": url}],
            task_id="web_tagging_single",
        )
        if results:
            return results[0]
        return {"success": False, "url": url, "error": "扫描失败"}


async def run_web_tagging_pipeline(
    db: AsyncIOMotorDatabase,
    app_config: Any,
    project_id: str,
    company_name: str,
    max_urls: int = 50,
    max_tagging_urls: int = 10,
    task_id: str = "",
) -> dict[str, Any]:
    """运行 Web Tagging 流水线的便捷函数"""
    pipeline = WebTaggingPipeline(db, app_config)
    return await pipeline.run_pipeline(
        project_id=project_id,
        company_name=company_name,
        max_urls=max_urls,
        max_tagging_urls=max_tagging_urls,
        task_id=task_id,
    )
