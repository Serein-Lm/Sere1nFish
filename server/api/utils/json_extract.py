from __future__ import annotations

import json
from typing import Any


JsonValue = dict[str, Any] | list[Any]


def extract_json_value(text: str) -> JsonValue:
    """Extract the first JSON object or array from a model response."""
    if not text or not str(text).strip():
        raise ValueError("模型输出为空")

    content = str(text).strip()
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, (dict, list)):
        return parsed

    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char not in "{[":
            continue
        try:
            candidate, _ = decoder.raw_decode(content[index:])
        except (TypeError, ValueError):
            continue
        if isinstance(candidate, (dict, list)):
            return candidate

    raise ValueError("未找到 JSON 对象或数组")


def extract_json_object(text: str) -> dict:
    """从模型输出中尽量提取 JSON 对象。"""
    obj = extract_json_value(text)
    if not isinstance(obj, dict):
        raise ValueError("JSON 顶层不是对象")
    return obj
