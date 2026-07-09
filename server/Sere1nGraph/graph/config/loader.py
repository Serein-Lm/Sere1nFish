"""
配置加载工具。

- `load_config() -> AppConfig`
- `load_config_from_data(data) -> AppConfig`

运行配置统一来自前端写入的 MongoDB 加密配置。旧 JSON 文件路径入口已下线。
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import fields
from copy import deepcopy
from typing import Any, Optional
import os

from .models import (
    AppConfig,
    RuntimeConfig,
    ModelsConfig,
    MobileConfig,
    MobileVideoConfig,
    ToolsConfig,
    McpServerConfig,
    TianyanchaConfig,
    LangSmithConfig,
    MongodbConfig,
)


def _known_dataclass_kwargs(cls, data: dict[str, Any]) -> dict[str, Any]:
    allowed = {item.name for item in fields(cls)}
    return {key: value for key, value in (data or {}).items() if key in allowed}


def _get_default_config_path() -> Path:
    """返回旧版默认配置文件路径，仅给旧测试/提示信息兼容使用。"""
    return Path(__file__).resolve().parents[3] / "config.json"


def get_config_path(config_path: Optional[str] = None) -> Path:
    """返回旧版 config.json 路径；运行时业务配置不应调用它。"""
    if config_path is None:
        return _get_default_config_path()
    return Path(config_path)


def _apply_env_overrides(runtime_section: dict[str, Any], mongodb_section: dict[str, Any]) -> None:
    """文件引导模式下允许环境变量覆盖连接信息。数据库运行时配置不调用它。"""
    if os.getenv("MONGODB_URI"):
        mongodb_section["uri"] = os.environ["MONGODB_URI"]
    if os.getenv("MONGODB_DATABASE"):
        mongodb_section["database_name"] = os.environ["MONGODB_DATABASE"]
    if os.getenv("MONGODB_USERNAME"):
        mongodb_section["username"] = os.environ["MONGODB_USERNAME"]
    if os.getenv("MONGODB_PASSWORD"):
        mongodb_section["password"] = os.environ["MONGODB_PASSWORD"]
    if os.getenv("MONGODB_AUTH_SOURCE"):
        mongodb_section["auth_source"] = os.environ["MONGODB_AUTH_SOURCE"]
    if os.getenv("MONGODB_DIRECT"):
        mongodb_section["direct"] = os.environ["MONGODB_DIRECT"].lower() in {"1", "true", "yes"}
    if os.getenv("OPENAI_BASE_URL"):
        runtime_section["base_url"] = os.environ["OPENAI_BASE_URL"]
    if os.getenv("OPENAI_API_KEY"):
        runtime_section["api_key"] = os.environ["OPENAI_API_KEY"]


def load_config_from_data(data: dict[str, Any] | None, *, apply_env_overrides: bool = False) -> AppConfig:
    """从字典加载配置。数据库配置入口使用此函数，避免再依赖 config.json。"""
    data = deepcopy(data or {})

    app_section = data.get("app", {}) or {}
    runtime_section = data.get("runtime", {}) or {}
    mcp_servers_section = data.get("mcpServers", {}) or {}
    tools_section = data.get("tools", {}) or {}
    langsmith_section = data.get("langsmith", {}) or {}
    mongodb_section = data.get("mongodb", {}) or {}
    if apply_env_overrides:
        _apply_env_overrides(runtime_section, mongodb_section)

    # 解析 runtime 配置（包含 models）
    models_section = runtime_section.get("models", {}) or {}
    models = ModelsConfig(**_known_dataclass_kwargs(ModelsConfig, models_section))
    runtime = RuntimeConfig(
        base_url=runtime_section.get("base_url"),
        api_key=runtime_section.get("api_key"),
        models=models,
        agent_timeout=int(runtime_section.get("agent_timeout", 500)),
        max_tokens=int(runtime_section.get("max_tokens", 3000)),
        temperature=float(runtime_section.get("temperature", 0.0)),
        top_p=float(runtime_section.get("top_p", 0.85)),
        frequency_penalty=float(runtime_section.get("frequency_penalty", 0.2)),
    )

    mobile_section = data.get("mobile", {}) or {}
    video_section = mobile_section.get("video", {}) or {}
    mobile = MobileConfig(
        video=MobileVideoConfig(
            max_size=int(video_section.get("max_size", 1920)),
            bit_rate=int(video_section.get("bit_rate", 8_000_000)),
            max_fps=int(video_section.get("max_fps", 60)),
            downsize_on_error=bool(video_section.get("downsize_on_error", False)),
        ),
        adb_timeout=int(mobile_section.get("adb_timeout", 30)),
        executor_max_tokens=(
            int(mobile_section["executor_max_tokens"])
            if mobile_section.get("executor_max_tokens") is not None
            else None
        ),
    )

    # 解析 mcpServers: { "name": { ... } } 格式
    mcp_servers: dict[str, McpServerConfig] = {}
    for name, cfg in mcp_servers_section.items():
        transport = cfg.get("transport", "stdio")
        mcp_servers[name] = McpServerConfig(
            name=name,
            transport=transport,
            command=cfg.get("command"),
            args=cfg.get("args", []),
            env=cfg.get("env", {}),
            url=cfg.get("url"),
        )

    tianyancha_section = tools_section.get("tianyancha") or {}
    tianyancha = TianyanchaConfig(**_known_dataclass_kwargs(TianyanchaConfig, tianyancha_section))
    tools = ToolsConfig(tianyancha=tianyancha)

    langsmith = LangSmithConfig(**_known_dataclass_kwargs(LangSmithConfig, langsmith_section))
    mongodb = MongodbConfig(**_known_dataclass_kwargs(MongodbConfig, mongodb_section))

    return AppConfig(
        name=app_section.get("name", "LangGraph App"),
        runtime=runtime,
        mobile=mobile,
        mcp_servers=mcp_servers,
        tools=tools,
        langsmith=langsmith,
        mongodb=mongodb,
    )


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """
    返回环境变量覆盖后的默认 AppConfig。

    旧文件配置入口已下线：调用方不得再传 `config_path`。运行时业务配置必须通过
    `api.services.runtime_config.get_runtime_app_config()` 从 MongoDB 加密配置读取。
    """
    if config_path is not None:
        raise ValueError("本地配置文件入口已下线；请在前端配置页写入 MongoDB 加密配置。")

    return load_config_from_data({}, apply_env_overrides=True)
