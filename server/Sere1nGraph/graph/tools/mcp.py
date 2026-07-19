"""
MCP 客户端配置辅助模块。

职责：
- 从 AppConfig 中解析 MCP Server 配置；
- 基于配置构造适合 MultiServerMCPClient 的 connections 字典。

支持的传输方式：
- stdio: 通过子进程启动 MCP server（command + args）
- http: 通过 HTTP 连接远程 MCP server（url）
- sse: 通过 SSE 连接远程 MCP server（url）- 已被 MCP 规范废弃，建议用 http
"""

from __future__ import annotations

from typing import Any, Iterable

from ..config.models import AppConfig, McpServerConfig


CHROME_DEVTOOLS_MCP_COMMAND = "chrome-devtools-mcp"


def get_mcp_servers(
    app_config: AppConfig,
    server_names: Iterable[str] | str | None = None,
) -> dict[str, McpServerConfig]:
    """
    从 AppConfig 中获取 MCP Server 配置，可按名称筛选。
    """
    all_servers = app_config.mcp_servers or {}

    if server_names is None:
        return dict(all_servers)

    if isinstance(server_names, str):
        name_set = {server_names}
    else:
        name_set = set(server_names)

    return {k: v for k, v in all_servers.items() if k in name_set}


def build_mcp_connections(
    app_config: AppConfig,
    server_names: Iterable[str] | str | None = None,
) -> dict[str, dict[str, Any]]:
    """
    基于 AppConfig 构造 MultiServerMCPClient 需要的 connections 字典。

    返回形如：
        {
            "xhs": {"transport": "http", "url": "http://localhost:18060/mcp"},
            "playwright": {"transport": "stdio", "command": "npx", "args": [...]},
        }
    """
    servers = get_mcp_servers(app_config, server_names=server_names)
    return {
        name: (
            _build_chrome_connection_dict(cfg)
            if name == "chrome-devtools" and cfg.transport == "stdio"
            else _build_connection_dict(cfg)
        )
        for name, cfg in servers.items()
    }


def _build_connection_dict(cfg: McpServerConfig) -> dict[str, Any]:
    """
    根据 transport 类型构建连接参数字典。
    """
    result: dict[str, Any] = {"transport": cfg.transport}

    if cfg.transport == "stdio":
        if cfg.command:
            result["command"] = cfg.command
        if cfg.args:
            result["args"] = cfg.args
        if cfg.env:
            result["env"] = cfg.env
    else:
        # http / sse 模式
        if cfg.url:
            result["url"] = cfg.url

    return result


def _build_chrome_connection_dict(
    cfg: McpServerConfig,
) -> dict[str, Any]:
    """Use the image-pinned executable instead of racing through shared npx."""
    result = _build_connection_dict(cfg)
    result["command"] = CHROME_DEVTOOLS_MCP_COMMAND
    result["args"] = [
        arg
        for arg in list(cfg.args or [])
        if arg not in {"-y", "--yes", "--"}
        and not str(arg).startswith("chrome-devtools-mcp@")
    ]
    return result


def build_chrome_mcp_connection(browser_url: str) -> dict[str, dict[str, Any]]:
    """
    构建连接到指定 Docker Chrome 容器的 MCP 配置。

    chrome-devtools-mcp 通过 --wsEndpoint 直接连接 Chrome 的 CDP WebSocket 代理。
    DockerProvider 返回 ws://host:{api_port}/cdp-proxy，直接传给 MCP。

    Args:
        browser_url: Docker Chrome 容器的 WS 代理地址，如 "ws://localhost:8251/cdp-proxy"

    Returns:
        可直接传给 MultiServerMCPClient 的 connections 字典
    """
    return {
        "chrome-devtools": {
            "transport": "stdio",
            "command": CHROME_DEVTOOLS_MCP_COMMAND,
            "args": [
                f"--wsEndpoint={browser_url}",
            ],
        }
    }
