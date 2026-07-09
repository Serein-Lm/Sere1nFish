"""手机采集任务运行时包。"""
from core.mobile.collect.pipeline import (
    run_collect_task,
    request_stop,
    is_running,
)

__all__ = ["run_collect_task", "request_stop", "is_running"]
