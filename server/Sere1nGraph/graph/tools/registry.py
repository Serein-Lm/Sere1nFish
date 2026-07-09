"""
工具注册中心定义。

这里给出一个极简版本的 `ToolRegistry`，后续可以对接 LangChain / MCP。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Mapping


class ToolsetName(str, Enum):
    BUILTIN = "builtin"
    MCP = "mcp"


@dataclass
class ToolRegistry:
    """
    简单的工具注册中心。
    """

    tools: Dict[str, Callable[..., Any]] = field(default_factory=dict)

    def register(self, name: str, fn: Callable[..., Any]) -> None:
        self.tools[name] = fn

    def get(self, name: str) -> Callable[..., Any]:
        if name not in self.tools:
            raise KeyError(f"Tool '{name}' not registered")
        return self.tools[name]

    def all(self) -> Mapping[str, Callable[..., Any]]:
        return dict(self.tools)


