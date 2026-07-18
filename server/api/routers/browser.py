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
        return {
            "mode": "docker" if containers is not None else "local",
            "containers": containers or [],
            "total": len(containers) if containers else 0,
            "busy": sum(1 for c in (containers or []) if c.get("status") == "busy"),
            "idle": sum(1 for c in (containers or []) if c.get("status") == "idle"),
        }
    except Exception as e:
        return {"mode": "local", "containers": [], "error": str(e)}


@router.get("/pool/config")
async def pool_config():
    """获取 Docker Chrome 配置"""
    try:
        from browser_manager.provider import _load_docker_config
        config = _load_docker_config()
        return {
            "enabled": config.enabled,
            "image": config.image,
            "max_containers": config.max_containers,
            "bulk_container_limit": config.bulk_container_limit,
            "reserved_non_bulk_containers": (
                config.normalized_reserved_non_bulk_containers
            ),
            "idle_timeout": config.idle_timeout,
            "screen_width": config.screen_width,
            "screen_height": config.screen_height,
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
