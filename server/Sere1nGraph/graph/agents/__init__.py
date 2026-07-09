"""
Agent 工厂与相关封装。

目前提供：
- `create_browser_agent(app_config, mcp_tools)`：创建基于 MCP（如 Playwright）的浏览器 Agent 节点。
- `create_xhs_agent(app_config, mcp_tools, server_name="xhs")`：创建小红书信息收集 Agent。
"""

from .factory import create_browser_agent, create_xhs_agent

__all__ = ["create_browser_agent", "create_xhs_agent"]


