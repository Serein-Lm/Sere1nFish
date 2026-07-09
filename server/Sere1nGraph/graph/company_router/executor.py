"""
节点执行器

根据 CompanyRouter 的输出，调用后续的信息收集节点（XHS/Douyin/WebTagging Pipeline）
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional, Callable, Awaitable
from dataclasses import dataclass, field

from .schemas import (
    SearchStrategy,
    NodeConfig,
    XhsSearchParams,
    DouyinSearchParams,
    WebTaggingParams,
    BiddingSearchParams,
)
from .router import CompanyRouterResult


@dataclass
class NodeResult:
    """单个节点执行结果"""
    node_name: str
    success: bool
    data: Any = None
    error: Optional[str] = None


@dataclass
class ExecutionResult:
    """整体执行结果"""
    success: bool
    results: dict[str, NodeResult] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    
    def get_data(self, node_name: str) -> Any:
        """获取指定节点的数据"""
        if node_name in self.results:
            return self.results[node_name].data
        return None


# 节点处理函数类型
NodeHandler = Callable[[NodeConfig, dict], Awaitable[Any]]


class NodeExecutor:
    """
    节点执行器
    
    根据路由结果，按优先级调用各信息收集节点
    
    必需的 context 参数:
    - db: AsyncIOMotorDatabase 实例
    - app_config: 应用配置
    - project_id: 项目 ID
    """
    
    def __init__(self):
        self._handlers: dict[str, NodeHandler] = {}
    
    def register_handler(self, node_name: str, handler: NodeHandler):
        """
        注册节点处理函数
        
        Args:
            node_name: 节点名称 (xhs/douyin/web_tagging/bidding/weixin)
            handler: 异步处理函数，接收 (NodeConfig, context) 返回结果
        """
        self._handlers[node_name] = handler
    
    async def execute(
        self,
        router_result: CompanyRouterResult,
        context: Optional[dict] = None,
        parallel: bool = False,  # 默认顺序执行，避免资源竞争
    ) -> ExecutionResult:
        """
        执行所有启用的节点
        
        Args:
            router_result: 路由结果
            context: 上下文信息，必须包含 db, app_config, project_id
            parallel: 是否并行执行（默认 False，顺序执行更稳定）
        
        Returns:
            ExecutionResult: 执行结果
        """
        if not router_result.success:
            return ExecutionResult(
                success=False,
                errors=[router_result.error or "Router failed"],
            )
        
        context = context or {}
        
        # 验证必需参数
        required = ["db", "app_config", "project_id"]
        missing = [k for k in required if k not in context]
        if missing:
            return ExecutionResult(
                success=False,
                errors=[f"Missing required context: {missing}"],
            )
        
        context["company_profile"] = router_result.company_profile
        
        strategy = router_result.search_strategy
        enabled_nodes = router_result.enabled_nodes
        
        if parallel:
            return await self._execute_parallel(strategy, enabled_nodes, context)
        else:
            return await self._execute_sequential(strategy, enabled_nodes, context)
    
    async def _execute_parallel(
        self,
        strategy: SearchStrategy,
        enabled_nodes: list[str],
        context: dict,
    ) -> ExecutionResult:
        """并行执行所有节点"""
        tasks = []
        node_names = []
        
        for node_name in enabled_nodes:
            if node_name not in self._handlers:
                continue
            
            node_config: NodeConfig = getattr(strategy, node_name)
            handler = self._handlers[node_name]
            
            tasks.append(self._safe_execute(node_name, handler, node_config, context))
            node_names.append(node_name)
        
        if not tasks:
            return ExecutionResult(success=True)
        
        results = await asyncio.gather(*tasks)
        
        execution_result = ExecutionResult(success=True)
        for node_name, result in zip(node_names, results):
            execution_result.results[node_name] = result
            if not result.success:
                execution_result.errors.append(f"{node_name}: {result.error}")
        
        return execution_result
    
    async def _execute_sequential(
        self,
        strategy: SearchStrategy,
        enabled_nodes: list[str],
        context: dict,
    ) -> ExecutionResult:
        """按优先级顺序执行"""
        execution_result = ExecutionResult(success=True)
        
        for node_name in enabled_nodes:
            if node_name not in self._handlers:
                print(f"[Executor] 节点 {node_name} 无 handler，跳过")
                continue
            
            node_config: NodeConfig = getattr(strategy, node_name)
            handler = self._handlers[node_name]
            
            print(f"[Executor] 开始执行节点: {node_name}")
            result = await self._safe_execute(node_name, handler, node_config, context)
            execution_result.results[node_name] = result
            
            if result.success:
                print(f"[Executor] 节点 {node_name} 执行成功")
            else:
                print(f"[Executor] 节点 {node_name} 执行失败: {result.error}")
                execution_result.errors.append(f"{node_name}: {result.error}")
        
        return execution_result
    
    async def _safe_execute(
        self,
        node_name: str,
        handler: NodeHandler,
        config: NodeConfig,
        context: dict,
    ) -> NodeResult:
        """安全执行单个节点"""
        try:
            data = await handler(config, context)
            return NodeResult(
                node_name=node_name,
                success=True,
                data=data,
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            return NodeResult(
                node_name=node_name,
                success=False,
                error=str(e),
            )


# ============ 实际 Pipeline 对接的 Handler ============

async def xhs_search_handler(config: NodeConfig, context: dict) -> Any:
    """
    小红书搜索处理 - 对接 XhsPipeline
    
    对每个关键词运行完整的 XHS 流水线
    """
    from api.services.xhs_pipeline import XhsPipeline
    
    db = context["db"]
    app_config = context["app_config"]
    project_id = context["project_id"]
    company_profile = context.get("company_profile")
    
    pipeline = XhsPipeline(db, app_config)
    results = []
    
    for keyword in config.keywords:
        # 生成任务 ID
        task_id = f"xhs_{uuid.uuid4().hex[:8]}"
        
        print(f"[XHS] 开始搜索关键词: {keyword}")
        
        try:
            result = await pipeline.run_pipeline(
                task_id=task_id,
                project_id=project_id,
                keyword=keyword,
                max_notes=config.params.get("max_notes", 20),
                attention_threshold=config.params.get("attention_threshold", 60),
                sort_type=config.params.get("sort_type", "time_descending"),
                enable_comments=config.params.get("enable_comments", True),
                enable_images=config.params.get("enable_images", False),
            )
            results.append({"keyword": keyword, "result": result})
            print(f"[XHS] 关键词 {keyword} 完成: {result.get('notes_count', 0)} 笔记")
        except Exception as e:
            print(f"[XHS] 关键词 {keyword} 失败: {e}")
            results.append({"keyword": keyword, "error": str(e)})
    
    return {
        "type": "xhs_pipeline",
        "keywords_count": len(config.keywords),
        "results": results,
    }


async def douyin_search_handler(config: NodeConfig, context: dict) -> Any:
    """
    抖音搜索处理 - 对接 DouyinPipeline
    
    对每个关键词运行完整的抖音流水线
    """
    from api.services.douyin_pipeline import DouyinPipeline
    
    db = context["db"]
    app_config = context["app_config"]
    project_id = context["project_id"]
    
    pipeline = DouyinPipeline(db, app_config)
    results = []
    
    for keyword in config.keywords:
        print(f"[Douyin] 开始搜索关键词: {keyword}")
        
        try:
            result = await pipeline.run_pipeline(
                project_id=project_id,
                keyword=keyword,
                max_videos=config.params.get("max_videos", 20),
                publish_time=config.params.get("publish_time", 0),
                enable_profile=config.params.get("enable_profile", True),
            )
            results.append({"keyword": keyword, "result": result})
            print(f"[Douyin] 关键词 {keyword} 完成: {result.get('videos_count', 0)} 视频")
        except Exception as e:
            print(f"[Douyin] 关键词 {keyword} 失败: {e}")
            results.append({"keyword": keyword, "error": str(e)})
    
    return {
        "type": "douyin_pipeline",
        "keywords_count": len(config.keywords),
        "results": results,
    }


async def web_tagging_handler(config: NodeConfig, context: dict) -> Any:
    """
    官网爬取处理 - 对接 WebTaggingPipeline
    
    使用 ICP 备案名进行 Hunter 查询 + Agent 打标
    """
    from api.services.web_tagging_pipeline import WebTaggingPipeline
    
    db = context["db"]
    app_config = context["app_config"]
    project_id = context["project_id"]
    company_profile = context.get("company_profile")
    
    # 获取 ICP 备案名
    icp_name = company_profile.icp_name if company_profile else None
    if not icp_name:
        return {"type": "web_tagging", "error": "缺少 ICP 备案名"}
    
    print(f"[WebTagging] 开始查询公司: {icp_name}")
    
    pipeline = WebTaggingPipeline(db, app_config)
    result = await pipeline.run_pipeline(
        project_id=project_id,
        company_name=icp_name,
        max_urls=config.params.get("max_urls", 50),
        max_tagging_urls=config.params.get("max_tagging_urls", 10),
    )
    
    print(f"[WebTagging] 完成: 存活 {result.get('alive_count', 0)} 个 URL, 打标 {result.get('tagged_count', 0)} 个")
    
    return {
        "type": "web_tagging_pipeline",
        "company_name": icp_name,
        "result": result,
    }


async def bidding_search_handler(config: NodeConfig, context: dict) -> Any:
    """
    招投标搜索处理 - 暂未实现
    """
    # TODO: 实现招投标搜索 pipeline
    return {
        "type": "bidding_search",
        "status": "not_implemented",
        "keywords": config.keywords,
    }


def create_default_executor() -> NodeExecutor:
    """创建带有实际 Pipeline 对接的执行器"""
    executor = NodeExecutor()
    executor.register_handler("xhs", xhs_search_handler)
    executor.register_handler("douyin", douyin_search_handler)
    executor.register_handler("web_tagging", web_tagging_handler)
    executor.register_handler("bidding", bidding_search_handler)
    return executor
