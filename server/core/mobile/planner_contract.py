"""Normalize model plans at the boundary shared by planning and replanning."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, field_validator


_INSTRUCTION_FIELDS = ("description", "instruction", "task", "subtask", "intent", "action")


def _instruction_text(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    elif isinstance(value, dict):
        parts = list(dict.fromkeys(
            value[key].strip()
            for key in _INSTRUCTION_FIELDS
            if isinstance(value.get(key), str) and value[key].strip()
        ))
        if parts:
            arguments = {key: item for key, item in value.items() if key not in _INSTRUCTION_FIELDS}
            if arguments:
                parts.append(json.dumps(arguments, ensure_ascii=False))
            return "；".join(parts)
    raise ValueError("每个手机子任务必须包含非空的执行指令")


class TaskPlan(BaseModel):
    subtasks: list[str] = Field(
        description="有序的自然语言子任务字符串列表；每项为完整指令，不使用对象或动作字典"
    )

    @field_validator("subtasks", mode="before")
    @classmethod
    def normalize_instructions(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [_instruction_text(item) for item in value]
