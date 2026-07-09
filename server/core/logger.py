"""
统一日志管理模块

使用方式:
    from core.logger import get_logger
    logger = get_logger("my_module")
    logger.debug("调试信息")
    logger.info("普通信息")
    logger.warning("警告")
    logger.error("错误")

配置 (环境变量 LOG_*):
    enabled       : 总开关，False 时所有日志静默
    level         : 根日志级别 (DEBUG / INFO / WARNING / ERROR)
    console_enabled: 是否输出到终端
    console_level  : 终端最低级别
    file_enabled   : 是否写入文件
    file_level     : 文件最低级别
    log_dir        : 日志目录 (默认 logs/)
    max_file_size_mb: 单文件最大 MB
    backup_count   : 保留的历史文件数
"""

import logging
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path
from functools import lru_cache
from typing import Optional

# ── 默认配置 ──────────────────────────────────────────────
_DEFAULTS = {
    "enabled": True,
    "level": "DEBUG",
    "console_enabled": False,
    "console_level": "INFO",
    "file_enabled": True,
    "file_level": "DEBUG",
    "log_dir": "logs",
    "max_file_size_mb": 10,
    "backup_count": 5,
}


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def _load_log_config() -> dict:
    """从环境变量读取 logging 配置，读不到就用默认值。"""
    cfg = dict(_DEFAULTS)
    env_mapping = {
        "LOG_ENABLED": ("enabled", lambda v: v.lower() not in {"0", "false", "no"}),
        "LOG_LEVEL": ("level", str),
        "LOG_CONSOLE_ENABLED": ("console_enabled", lambda v: v.lower() in {"1", "true", "yes"}),
        "LOG_CONSOLE_LEVEL": ("console_level", str),
        "LOG_FILE_ENABLED": ("file_enabled", lambda v: v.lower() not in {"0", "false", "no"}),
        "LOG_FILE_LEVEL": ("file_level", str),
        "LOG_DIR": ("log_dir", str),
        "LOG_MAX_FILE_SIZE_MB": ("max_file_size_mb", int),
        "LOG_BACKUP_COUNT": ("backup_count", int),
    }
    for env_name, (key, caster) in env_mapping.items():
        value = os.getenv(env_name)
        if value not in (None, ""):
            try:
                cfg[key] = caster(value)
            except Exception:
                pass
    return cfg


class _NullHandler(logging.Handler):
    """静默 handler，日志总开关关闭时使用"""
    def emit(self, record):
        pass


# ── 自定义 NOTICE 级别（25，介于 INFO 和 WARNING 之间）──
# 用于任务下发、启动、完成等关键事件，强制输出到控制台
NOTICE = 25
logging.addLevelName(NOTICE, "NOTICE")


def _notice(self, message, *args, **kwargs):
    if self.isEnabledFor(NOTICE):
        self._log(NOTICE, message, args, **kwargs)


logging.Logger.notice = _notice


_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_initialized = False


def _ensure_init():
    """首次调用时初始化根 logger（只执行一次）"""
    global _initialized
    if _initialized:
        return
    _initialized = True

    cfg = _load_log_config()

    # 总开关
    if not cfg.get("enabled", True):
        root = logging.getLogger()
        root.addHandler(_NullHandler())
        root.setLevel(logging.CRITICAL + 1)
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, cfg["level"].upper(), logging.DEBUG))

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # ── Console Handler（受配置控制）──
    if cfg.get("console_enabled", False):
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(getattr(logging, cfg["console_level"].upper(), logging.INFO))
        ch.setFormatter(formatter)
        root.addHandler(ch)
    else:
        # ── NOTICE Handler（强制控制台，不受 console_enabled 控制）──
        # 即使控制台关闭，NOTICE 及以上也会打到终端
        nh = logging.StreamHandler(sys.stdout)
        nh.setLevel(NOTICE)
        nh.setFormatter(formatter)
        root.addHandler(nh)

    # ── File Handler ──
    if cfg.get("file_enabled", True):
        log_dir = _PROJECT_ROOT / cfg.get("log_dir", "logs")
        log_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        log_file = log_dir / f"{today}.log"

        max_bytes = cfg.get("max_file_size_mb", 10) * 1024 * 1024
        backup = cfg.get("backup_count", 5)

        fh = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup,
            encoding="utf-8",
        )
        fh.setLevel(getattr(logging, cfg["file_level"].upper(), logging.DEBUG))
        fh.setFormatter(formatter)
        root.addHandler(fh)

    # 降低第三方库噪音
    for noisy in (
        "httpx", "httpcore", "urllib3", "asyncio", "uvicorn.access",
        "pymongo.topology", "pymongo.connection", "pymongo.command",
        "pymongo.serverSelection", "pymongo.ocsp_support",
        "openai._base_client", "openai._legacy_response",
        "openai", "openai.api_requestor",
        "watchfiles", "watchfiles.main",
        "docker.utils.config", "docker.auth",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    获取一个命名 logger。

    用法:
        from core.logger import get_logger
        logger = get_logger("xhs_pipeline")
        logger.info("开始处理")
    """
    _ensure_init()
    return logging.getLogger(name)


def reconfigure():
    """
    运行时重新加载配置（热更新场景）。
    清除缓存后重新初始化。
    """
    global _initialized
    _load_log_config.cache_clear()
    # 清除已有 handlers
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    _initialized = False
    _ensure_init()
