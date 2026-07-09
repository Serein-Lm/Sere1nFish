from __future__ import annotations

import json
import re


def extract_json_object(text: str) -> dict:
    """从模型输出中尽量提取 JSON 对象。"""
    if not text or not str(text).strip():
        raise ValueError("模型输出为空")

    s = str(text).strip()

    # 1) 直接尝试整体解析
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 2) 尝试从文本中截取第一个 {...} 块
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        raise ValueError("未找到 JSON 对象")

    candidate = m.group(0)
    obj = json.loads(candidate)
    if not isinstance(obj, dict):
        raise ValueError("JSON 顶层不是对象")
    return obj
