"""
workflow 编排模块。

- events.py: 统一 SSE 事件构造
- executor.py: 统一执行入口
- streaming.py: 事件队列
- router.py: 多源路由工作流
- copywriting.py: 文案生成工作流

使用方式：
    from workflow import execute_stream, list_workflows
    
    async for event in execute_stream("router", query, app_config):
        print(event)
"""

from .executor import execute_stream, list_workflows, workflow_exists  # noqa: F401
from .events import event, start, update, content, end, error, ping  # noqa: F401


