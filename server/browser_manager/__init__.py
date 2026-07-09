"""
浏览器容器管理模块

独立于 MediaCrawler 的浏览器生命周期管理层。
负责 Chrome Docker 容器的创建、连接、释放、销毁。
"""

from .provider import (
    BrowserProvider,
    LocalProvider,
    DockerProvider,
    ChromeDockerConfig,
    ContainerInfo,
    configure_browser_provider,
    get_browser_provider,
    shutdown_provider,
)

__all__ = [
    "BrowserProvider",
    "LocalProvider",
    "DockerProvider",
    "ChromeDockerConfig",
    "ContainerInfo",
    "configure_browser_provider",
    "get_browser_provider",
    "shutdown_provider",
]
