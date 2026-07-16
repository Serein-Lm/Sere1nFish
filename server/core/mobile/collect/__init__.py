"""手机采集任务运行时包。

使用惰性入口避免通用来源文档服务复用 contacts 时触发 pipeline 循环导入。
"""


async def run_collect_task(*args, **kwargs):
    from core.mobile.collect.pipeline import run_collect_task as implementation

    return await implementation(*args, **kwargs)


def request_stop(run_task_id: str) -> bool:
    from core.mobile.collect.pipeline import request_stop as implementation

    return implementation(run_task_id)


def is_running(run_task_id: str) -> bool:
    from core.mobile.collect.pipeline import is_running as implementation

    return implementation(run_task_id)

__all__ = ["run_collect_task", "request_stop", "is_running"]
