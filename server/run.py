"""
启动脚本（热更新 + 仅本地访问）
"""

import resource
import uvicorn
from pathlib import Path
from api.config import get_settings
from core.logger import get_logger

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
    project_root = Path(__file__).parent
    
    logger.info(f"🚀 启动服务: http://{settings.HOST}:{settings.PORT}")
    logger.info(f"📚 API 文档: http://{settings.HOST}:{settings.PORT}/docs")
    logger.info(f"🔐 JWT 密钥已随机生成")
    logger.info(f"👤 默认用户: admin / admin123")
    logger.debug(f"📁 监控目录: {project_root}")
    
    uvicorn.run(
        "api.main:socket_app",
        host=settings.HOST,  # 127.0.0.1 只允许本地访问
        port=settings.PORT,
        reload=True,  # 热更新
        reload_dirs=[str(project_root)],  # 监控整个项目目录
        reload_excludes=["*.pyc", "__pycache__", ".venv", ".git", "*.log"],  # 排除不需要监控的文件
        # 优雅关闭超时上限：WS/SSE 长连接不会自然断开，超时后强制断开旧进程，
        # 避免 reload 卡在 "Waiting for connections to close"，同时兜住容器停机/重启。
        timeout_graceful_shutdown=settings.GRACEFUL_SHUTDOWN_TIMEOUT,
    )
