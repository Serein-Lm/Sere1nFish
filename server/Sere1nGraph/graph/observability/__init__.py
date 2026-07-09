"""
观测层 — Token 使用、费用统计、时间追踪

层级结构：
  全局总和 → 项目 → 任务 → 流程(pipeline阶段) → Agent(单次LLM调用)

使用方式：
  一个 callback 注入到 LLM，自动追踪所有层级。
  业务代码只需要 push_context / pop_context 管理层级。
"""

from .tracker import TokenTracker, get_global_tracker
from .pricing import calc_cost

__all__ = ["TokenTracker", "get_global_tracker", "calc_cost"]
