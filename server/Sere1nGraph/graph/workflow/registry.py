"""
工作流注册配置。

集中管理所有 Agent 和 Graph 的映射配置，方便维护和扩展。

扩展方式：
1. 新增 Agent: 在 AGENT_REGISTRY 添加配置
2. 新增 Graph: 在 GRAPH_REGISTRY 添加配置
"""

from __future__ import annotations

from typing import Callable, Any


# ============ Agent 注册表 ============

AGENT_REGISTRY: dict[str, dict[str, Any]] = {
    "browser": {
        "factory": "agents.factory:create_browser_agent",
        "displayName": "🏷️ 官网打标",
        "icon": "globe",
        "description": "官网社工打标（结构化输出）"
    },
    "xhs": {
        "factory": "agents.factory:create_xhs_agent",
        "displayName": "📱 小红书",
        "icon": "book",
        "description": "搜索小红书用户评价和口碑"
    },
    "weixin": {
        "factory": "agents.factory:create_weixin_search_agent",
        "displayName": "💬 微信公众号",
        "icon": "message",
        "description": "搜索微信公众号文章"
    },
    "bid": {
        "factory": "agents.factory:create_bid_collect_agent",
        "displayName": "📋 招投标",
        "icon": "file",
        "description": "查询招投标信息"
    },
}


# ============ Graph 注册表 ============

GRAPH_REGISTRY: dict[str, dict[str, Any]] = {
    "assistant": {
        "module": "workflow.hub",
        "builder": "build_hub_graph",
        "displayName": "🧠 AI 中枢",
        "icon": "robot",
        "description": "综合个人助手：分发到数据、人设、内容和载荷专家；载荷专家支持公网检索与 Word 交付",
        "initial_state": {
            "query": "",
            "classifications": [],
            "results": [],
            "final_answer": "",
        }
    },
    "router": {
        "module": "workflow.router",
        "builder": "build_router_graph",
        "displayName": "🔍 多源路由",
        "icon": "search",
        "description": "智能路由到多个知识源并汇总结果",
        "initial_state": {
            "query": "",
            "classifications": [],
            "results": [],
            "final_answer": "",
            "copywriting": "",
        }
    },
    "copywriting": {
        "module": "workflow.copywriting",
        "builder": "build_copywriting_graph",
        "displayName": "✍️ 话术生成（Skill 驱动）",
        "icon": "edit",
        "description": "Skill 驱动的话术生成：场景伪造 → 话术生成 → 质疑应对 → 整合输出（Pydantic JSON 结构化输出）",
        "initial_state": {
            "synthesis_result": "",
            "selected_categories": [],
            "scenario_json": "",
            "script_json": "",
            "objection_json": "",
            "final_json": "",
        }
    },
}


# ============ 工具函数 ============

def _resolve_module(module_path: str):
    """
    导入注册表中的模块路径。

    注册表使用相对于 graph 包的短路径（如 "agents.factory"、"workflow.router"）。
    运行时 graph 包实际为 Sere1nGraph.graph.*，因此优先按包前缀解析，
    再回退到裸路径以兼容其他运行方式。
    """
    import importlib

    # __package__ 形如 "Sere1nGraph.graph.workflow"，去掉末段得到 graph 包前缀
    prefix = __package__.rsplit(".", 1)[0] if __package__ and "." in __package__ else ""
    candidates = []
    if prefix:
        candidates.append(f"{prefix}.{module_path}")
    candidates.append(module_path)

    last_err: Exception | None = None
    for candidate in candidates:
        try:
            return importlib.import_module(candidate)
        except ModuleNotFoundError as e:
            last_err = e
            continue
    raise last_err if last_err else ModuleNotFoundError(module_path)


def get_agent_factory(name: str) -> Callable | None:
    """
    获取 Agent 工厂函数。
    
    参数:
        name: Agent 名称
    
    返回:
        工厂函数，如果不存在返回 None
    """
    if name not in AGENT_REGISTRY:
        return None
    
    factory_path = AGENT_REGISTRY[name]["factory"]
    module_path, func_name = factory_path.split(":")
    
    module = _resolve_module(module_path)
    return getattr(module, func_name)


def get_graph_builder(name: str) -> tuple[Callable, dict] | None:
    """
    获取 Graph 构建函数和初始状态。
    
    参数:
        name: Graph 名称
    
    返回:
        (构建函数, 初始状态模板)，如果不存在返回 None
    """
    if name not in GRAPH_REGISTRY:
        return None
    
    config = GRAPH_REGISTRY[name]
    module_path = config["module"]
    func_name = config["builder"]
    
    module = _resolve_module(module_path)
    builder = getattr(module, func_name)
    
    return builder, config["initial_state"].copy()


def get_workflow_meta(name: str) -> dict | None:
    """
    获取工作流元信息。
    
    参数:
        name: 工作流名称
    
    返回:
        元信息字典，如果不存在返回 None
    """
    if name in AGENT_REGISTRY:
        meta = AGENT_REGISTRY[name]
        return {
            "name": name,
            "displayName": meta["displayName"],
            "icon": meta.get("icon"),
            "description": meta.get("description", ""),
            "type": "agent"
        }
    
    if name in GRAPH_REGISTRY:
        meta = GRAPH_REGISTRY[name]
        return {
            "name": name,
            "displayName": meta["displayName"],
            "icon": meta.get("icon"),
            "description": meta.get("description", ""),
            "type": "graph"
        }
    
    return None


def list_all_workflows() -> list[dict]:
    """
    列出所有已注册的工作流。
    
    返回:
        工作流信息列表
    """
    result = []
    
    for name in AGENT_REGISTRY:
        meta = get_workflow_meta(name)
        if meta:
            result.append(meta)
    
    for name in GRAPH_REGISTRY:
        meta = get_workflow_meta(name)
        if meta:
            result.append(meta)
    
    return result


def workflow_exists(name: str) -> bool:
    """检查工作流是否存在"""
    return name in AGENT_REGISTRY or name in GRAPH_REGISTRY


def is_agent(name: str) -> bool:
    """检查是否为 Agent 类型"""
    return name in AGENT_REGISTRY


def is_graph(name: str) -> bool:
    """检查是否为 Graph 类型"""
    return name in GRAPH_REGISTRY
