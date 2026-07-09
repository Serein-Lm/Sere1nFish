"""
公司信息路由器模块

轻量级 LLM Agent，负责：
1. 解析公司名称（ICP标准名、口语化名）
2. 判断行业、业务性质
3. 生成搜索策略和关键词
4. 决定启用哪些下游节点（xhs、douyin、web_tagging等）

使用示例：
```python
from Sere1nGraph.graph.company_router import (
    CompanyRouter,
    NodeExecutor,
    create_default_executor,
)

# 1. 创建路由器
router = CompanyRouter(app_config)

# 2. 分析公司
result = await router.route("b站")

# 3. 执行节点
executor = create_default_executor()
exec_result = await executor.execute(result)
```
"""

from .schemas import (
    CompanyProfile,
    SearchStrategy,
    NodeConfig,
    CompanyRouterOutput,
    IndustryType,
    BusinessNature,
    XhsSearchParams,
    DouyinSearchParams,
    WebTaggingParams,
    BiddingSearchParams,
)
from .keywords import KeywordLibrary, CustomKeywordLibrary
from .router import CompanyRouter, CompanyRouterResult, create_company_router
from .executor import (
    NodeExecutor,
    NodeResult,
    ExecutionResult,
    create_default_executor,
)

__all__ = [
    # 核心类
    "CompanyRouter",
    "CompanyRouterResult",
    "create_company_router",
    # 执行器
    "NodeExecutor",
    "NodeResult",
    "ExecutionResult",
    "create_default_executor",
    # 数据结构
    "CompanyProfile",
    "SearchStrategy",
    "NodeConfig",
    "CompanyRouterOutput",
    # 节点参数
    "XhsSearchParams",
    "DouyinSearchParams",
    "WebTaggingParams",
    "BiddingSearchParams",
    # 枚举
    "IndustryType",
    "BusinessNature",
    # 关键词库
    "KeywordLibrary",
    "CustomKeywordLibrary",
]
