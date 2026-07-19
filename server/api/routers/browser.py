"""
浏览器容器管理 API

提供 Chrome Docker 容器池的状态查询和管理接口。
"""

from fastapi import APIRouter

router = APIRouter(prefix="/browser", tags=["浏览器管理"])


@router.get("/pool/status")
async def pool_status():
    """获取 Chrome 容器池状态"""
    try:
        from browser_manager import get_browser_provider
        provider = get_browser_provider()
        containers = await provider.get_pool_status()
        response = {
            "mode": "docker" if containers is not None else "local",
            "containers": containers or [],
            "total": len(containers) if containers else 0,
            "busy": sum(1 for c in (containers or []) if c.get("status") == "busy"),
            "idle": sum(1 for c in (containers or []) if c.get("status") == "idle"),
        }
        if hasattr(provider, "capacity_status"):
            response["capacity"] = provider.capacity_status()
        return response
    except Exception as e:
        return {"mode": "local", "containers": [], "error": str(e)}


@router.get("/pool/config")
async def pool_config():
    """获取 Docker Chrome 配置"""
    try:
        from browser_manager import get_browser_provider
        from browser_manager.provider import DockerProvider, _load_docker_config

        provider = get_browser_provider()
        config = (
            provider.config
            if isinstance(provider, DockerProvider)
            else _load_docker_config()
        )
        return {
            "enabled": config.enabled,
            "image": config.image,
            "max_containers": config.max_containers,
            "bulk_container_limit": config.bulk_container_limit,
            "reserved_non_bulk_containers": (
                config.normalized_reserved_non_bulk_containers
            ),
            "idle_timeout": config.idle_timeout,
            "health_check_interval": config.health_check_interval,
            "wechat_article_lease_timeout": config.wechat_article_lease_timeout,
            "generic_busy_lease_timeout": config.generic_busy_lease_timeout,
            "screen_width": config.screen_width,
            "screen_height": config.screen_height,
            "warm_pool_size": config.warm_pool_size,
            "container_create_concurrency": (
                config.container_create_concurrency
            ),
            "container_health_concurrency": (
                config.container_health_concurrency
            ),
            "docker_api_timeout_seconds": config.docker_api_timeout_seconds,
            "memory_check_interval": config.memory_check_interval,
            "cdp_health_failure_threshold": config.cdp_health_failure_threshold,
            "host_memory_floor_mb": config.host_memory_floor_mb,
            "capacity": (
                provider.capacity_status()
                if isinstance(provider, DockerProvider)
                else {}
            ),
        }
    except Exception as e:
        return {"enabled": False, "error": str(e)}


@router.post("/pool/shutdown")
async def pool_shutdown():
    """关闭所有 Chrome 容器"""
    try:
        from browser_manager import get_browser_provider
        provider = get_browser_provider()
        await provider.shutdown()
        return {"status": "ok", "message": "所有容器已关闭"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
