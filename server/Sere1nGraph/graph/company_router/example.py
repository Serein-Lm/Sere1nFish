"""
CompanyRouter + NodeExecutor 使用示例

展示完整的信息收集流程:
目标公司名 -> CompanyRouter -> NodeExecutor -> 各 Pipeline
"""

import asyncio
from typing import Any


async def run_info_collection(
    company_name: str,
    db: Any,
    app_config: Any,
    project_id: str,
) -> dict[str, Any]:
    """
    运行完整的信息收集流程
    
    Args:
        company_name: 目标公司名（可以是口语名如"b站"，也可以是标准名）
        db: MongoDB 数据库实例
        app_config: 应用配置
        project_id: 项目 ID
    
    Returns:
        收集结果
    """
    from . import CompanyRouter, create_default_executor
    
    # 1. 创建路由器，分析公司
    print(f"\n{'='*50}")
    print(f"[Router] 开始分析公司: {company_name}")
    print(f"{'='*50}")
    
    router = CompanyRouter(app_config)
    router_result = await router.route(company_name)
    
    if not router_result.success:
        return {"success": False, "error": router_result.error}
    
    # 打印分析结果
    profile = router_result.company_profile
    print(f"\n[Router] 公司画像:")
    print(f"  - ICP 名称: {profile.icp_name}")
    print(f"  - 口语名称: {profile.colloquial_names}")
    print(f"  - 行业: {profile.industry}")
    print(f"  - 业务性质: {profile.business_nature}")
    print(f"  - 标签: {profile.tags}")
    
    print(f"\n[Router] 启用节点: {router_result.enabled_nodes}")
    print(f"[Router] 搜索关键词:")
    for node, keywords in router_result.all_keywords.items():
        print(f"  - {node}: {keywords[:3]}...")  # 只显示前3个
    
    # 2. 创建执行器，运行各节点
    print(f"\n{'='*50}")
    print(f"[Executor] 开始执行信息收集")
    print(f"{'='*50}")
    
    executor = create_default_executor()
    
    context = {
        "db": db,
        "app_config": app_config,
        "project_id": project_id,
    }
    
    exec_result = await executor.execute(router_result, context)
    
    # 3. 汇总结果
    print(f"\n{'='*50}")
    print(f"[Summary] 执行完成")
    print(f"{'='*50}")
    
    summary = {
        "success": exec_result.success,
        "company_profile": {
            "icp_name": profile.icp_name,
            "colloquial_names": profile.colloquial_names,
            "industry": profile.industry.value,
            "business_nature": profile.business_nature.value,
        },
        "enabled_nodes": router_result.enabled_nodes,
        "node_results": {},
        "errors": exec_result.errors,
    }
    
    for node_name, node_result in exec_result.results.items():
        summary["node_results"][node_name] = {
            "success": node_result.success,
            "data": node_result.data,
            "error": node_result.error,
        }
        
        if node_result.success:
            print(f"  ✓ {node_name}: 成功")
        else:
            print(f"  ✗ {node_name}: {node_result.error}")
    
    return summary


async def run_single_node(
    company_name: str,
    node_name: str,
    db: Any,
    app_config: Any,
    project_id: str,
) -> dict[str, Any]:
    """
    只运行单个节点
    
    Args:
        company_name: 目标公司名
        node_name: 节点名称 (xhs/douyin/web_tagging)
        db: MongoDB 数据库实例
        app_config: 应用配置
        project_id: 项目 ID
    """
    from . import CompanyRouter, NodeExecutor
    from .executor import xhs_search_handler, douyin_search_handler, web_tagging_handler
    
    # 路由
    router = CompanyRouter(app_config)
    router_result = await router.route(company_name)
    
    if not router_result.success:
        return {"success": False, "error": router_result.error}
    
    # 只注册指定节点
    executor = NodeExecutor()
    handlers = {
        "xhs": xhs_search_handler,
        "douyin": douyin_search_handler,
        "web_tagging": web_tagging_handler,
    }
    
    if node_name not in handlers:
        return {"success": False, "error": f"Unknown node: {node_name}"}
    
    executor.register_handler(node_name, handlers[node_name])
    
    # 强制只启用指定节点
    router_result.enabled_nodes = [node_name]
    
    context = {
        "db": db,
        "app_config": app_config,
        "project_id": project_id,
    }
    
    return await executor.execute(router_result, context)


# ============ 测试用例 ============

async def test_router_only():
    """测试路由器（不执行实际采集）"""
    from Sere1nGraph.graph.config.models import AppConfig
    
    # 加载配置
    app_config = AppConfig.from_yaml("Sere1nGraph/graph/config/config.yaml")
    
    from . import CompanyRouter
    
    router = CompanyRouter(app_config)
    
    # 测试不同类型的公司
    test_cases = [
        "b站",
        "首都机场",
        "招商银行",
        "北京市政府",
    ]
    
    for company in test_cases:
        print(f"\n{'='*40}")
        print(f"测试: {company}")
        print(f"{'='*40}")
        
        result = await router.route(company)
        
        if result.success:
            print(f"ICP: {result.company_profile.icp_name}")
            print(f"行业: {result.company_profile.industry}")
            print(f"启用节点: {result.enabled_nodes}")
            print(f"关键词: {list(result.all_keywords.keys())}")
        else:
            print(f"失败: {result.error}")


if __name__ == "__main__":
    asyncio.run(test_router_only())
