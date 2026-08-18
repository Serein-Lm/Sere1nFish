"""
启动脚本（热更新 + 仅本地访问）
"""

from pathlib import Path

import resource
import uvicorn

from api.config import get_settings
from core.logger import get_logger
from core.process_watchdog import ProcessWatchdogConfig, start_process_health_watchdog

logger = get_logger("startup")

# 提高文件描述符限制（并发浏览器、ADB 和长连接场景需要更多 fd）
try:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = min(1048576, hard)
    if soft < target:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        logger.info(f"文件描述符限制: {soft} → {target}")
except Exception as e:
    logger.warning(f"无法提高文件描述符限制: {e}")

if __name__ == "__main__":
    settings = get_settings()
    
    # 获取项目根目录
    # Resolve to an absolute path because uvicorn compares watched file parents
    # against these exclusions. A relative ``test_server`` path does not match
    # watchfiles' absolute events and used to restart live scans during pytest.
    project_root = Path(__file__).resolve().parent
    
    logger.info(f"启动服务: http://{settings.HOST}:{settings.PORT}")
    logger.info(f"API 文档: http://{settings.HOST}:{settings.PORT}/docs")
    logger.debug(f"监控目录: {project_root}")

    reload_excludes = [
        "*.pyc",
        "__pycache__",
        ".venv",
        ".git",
        "*.log",
        str(project_root / "test_server"),
        str(project_root / ".pytest_cache"),
        str(project_root / "AutoGLM-GUI-main" / "tests"),
    ]

    if settings.SERVER_WATCHDOG_ENABLED:
        start_process_health_watchdog(
            port=settings.PORT,
            config=ProcessWatchdogConfig(
                startup_grace_seconds=settings.SERVER_WATCHDOG_STARTUP_GRACE_SECONDS,
                interval_seconds=settings.SERVER_WATCHDOG_INTERVAL_SECONDS,
                probe_timeout_seconds=settings.SERVER_WATCHDOG_PROBE_TIMEOUT_SECONDS,
                failure_threshold=settings.SERVER_WATCHDOG_FAILURE_THRESHOLD,
            ),
        )
    
    uvicorn.run(
        "api.main:socket_app",
        host=settings.HOST,  # 127.0.0.1 只允许本地访问
        port=settings.PORT,
        reload=True,  # 热更新
        reload_dirs=[str(project_root)],  # 运行时源码保持热更新
        reload_excludes=reload_excludes,
        # 优雅关闭超时上限：WS/SSE 长连接不会自然断开，超时后强制断开旧进程，
        # 避免 reload 卡在 "Waiting for connections to close"，同时兜住容器停机/重启。
        timeout_graceful_shutdown=settings.GRACEFUL_SHUTDOWN_TIMEOUT,
    )
