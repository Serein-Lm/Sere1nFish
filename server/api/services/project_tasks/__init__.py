"""Project task capability registry, validation and submission services."""

from api.services.project_tasks.registry import (
    ProjectTaskDefinition,
    register_default_project_task_dispatchers,
)
from api.services.project_tasks.service import submit_project_task

__all__ = [
    "ProjectTaskDefinition",
    "register_default_project_task_dispatchers",
    "submit_project_task",
]
