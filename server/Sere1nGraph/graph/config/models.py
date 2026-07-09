"""
配置模型定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class ModelsConfig:
    """
    模型名称配置。
    
    - `default`: 默认 LLM 模型
    - `vision`: 视觉理解模型
    - `mobile_planner`: 手机规划层模型；未配置时回退到 default
    - `mobile_executor`: 手机执行层模型；未配置时回退到 vision
    - `mobile_screen`: 手机读屏/界面描述模型；未配置时回退到 mobile_executor/vision
    - `mobile_chat`: 手机聊天状态解析/轻量回复模型；未配置时回退到 default
    """
    default: str = "qwen3.7-plus"
    vision: str = "qwen3.7-plus"
    mobile_planner: Optional[str] = "qwen3.7-plus"
    mobile_executor: Optional[str] = "qwen3.7-plus"
    mobile_screen: Optional[str] = "qwen3.7-plus"
    mobile_chat: Optional[str] = "qwen3.7-plus"

    @property
    def mobile_planner_model(self) -> str:
        return self.mobile_planner or self.default

    @property
    def mobile_executor_model(self) -> str:
        return self.mobile_executor or self.vision

    @property
    def mobile_screen_model(self) -> str:
        return self.mobile_screen or self.mobile_executor_model

    @property
    def mobile_chat_model(self) -> str:
        return self.mobile_chat or self.default


@dataclass
class RuntimeConfig:
    """
    运行时相关配置（统一的 API 配置 + 模型分类）。

    - `base_url`：API Base URL
    - `api_key`：API Key
    - `models`：模型名称配置
    - `agent_timeout`：Agent 单次执行超时秒数（默认 500）
    - `max_tokens` / `temperature` / `top_p` / `frequency_penalty`：执行层 LLM 采样参数
    """

    base_url: Optional[str] = None
    api_key: Optional[str] = None
    models: ModelsConfig = field(default_factory=ModelsConfig)
    agent_timeout: int = 500
    max_tokens: int = 3000
    temperature: float = 0.0
    top_p: float = 0.85
    frequency_penalty: float = 0.2


@dataclass
class MobileVideoConfig:
    """scrcpy / Socket.IO 视频流默认参数（前端 connect-device 可覆盖）。"""

    max_size: int = 1920
    bit_rate: int = 8_000_000
    max_fps: int = 60
    downsize_on_error: bool = False


@dataclass
class MobileConfig:
    """手机子系统：视频、ADB 超时、远程保活等。"""

    video: MobileVideoConfig = field(default_factory=MobileVideoConfig)
    adb_timeout: int = 30
    executor_max_tokens: Optional[int] = None
    # 远程手机默认保活(低负载时 ADB 易断,必须常驻保活)
    keepalive_enabled: bool = True
    keepalive_interval_seconds: int = 90
    keepalive_screen_always_on: bool = True
    keepalive_reconnect: bool = True
    keepalive_probe_timeout: int = 5


@dataclass
class McpServerConfig:
    """
    单个 MCP Server 配置，支持 stdio / http / sse 三种传输方式。

    stdio 模式：
        - command: 启动命令
        - args: 命令参数列表
        - env: 环境变量（可选）

    http / sse 模式：
        - url: 端点地址
    """

    name: str
    transport: Literal["stdio", "http", "sse"] = "stdio"
    # stdio 模式
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    # http / sse 模式
    url: Optional[str] = None


@dataclass
class TianyanchaConfig:
    """天眼查开放平台相关配置。"""

    api_key: Optional[str] = None


@dataclass
class ToolsConfig:
    tianyancha: TianyanchaConfig = field(default_factory=TianyanchaConfig)


@dataclass
class LangSmithConfig:
    """LangSmith 相关配置。"""

    enabled: bool = False
    api_key: Optional[str] = None
    project: Optional[str] = None
    endpoint: Optional[str] = None


@dataclass
class MongodbConfig:
    """MongoDB 相关配置。"""

    uri: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    auth_source: Optional[str] = None
    database_name: Optional[str] = None
    direct: bool = False
    appname: Optional[str] = None
    max_pool_size: int = 100
    min_pool_size: int = 0
    max_idle_time_ms: int = 60000
    server_selection_timeout_ms: int = 5000
    connect_timeout_ms: int = 10000


@dataclass
class AppConfig:
    name: str = "LangGraph App"
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    mobile: MobileConfig = field(default_factory=MobileConfig)
    mcp_servers: Dict[str, McpServerConfig] = field(default_factory=dict)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    langsmith: LangSmithConfig = field(default_factory=LangSmithConfig)
    mongodb: MongodbConfig = field(default_factory=MongodbConfig)
