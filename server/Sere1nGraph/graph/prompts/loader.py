"""
提示词加载工具。

- `load_prompt(name)`：按名称加载单个 prompt。服务运行时优先读取 MongoDB 快照。
- `list_prompts()`：列出可用 prompt 名称。
"""

from __future__ import annotations

import os
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, List
import re


PROMPTS_DIR = Path(__file__).resolve().parent
_PROMPT_CACHE: dict[str, str] = {}
_PROMPT_CACHE_READY = False


def _prompt_path(name: str) -> Path:
    """
    解析 prompt 名称到具体的 markdown 路径。

    约定：
    - 简单名称：`chat` -> `prompts/chat/chat.md`
    - 显式相对路径：`chat/chat` 或 `chat/chat.md` -> `prompts/chat/chat.md`
    - 也兼容传入已经带扩展名的 name。
    """
    rel = Path(name)

    # 默认补全 .md 后缀
    if rel.suffix != ".md":
        rel = rel.with_suffix(".md")

    # 如果没有显式子目录，例如 "chat.md"，
    # 则按照 <name>/<name>.md 的规则映射
    if rel.parent == Path("."):
        rel = Path(rel.stem) / rel

    return PROMPTS_DIR / rel


def load_prompt(name: str) -> str:
    """
    从 `prompts/` 目录加载指定名称的 markdown 模板。

    支持的 name 形式：
    - "chat"              -> prompts/chat/chat.md
    - "chat/chat"         -> prompts/chat/chat.md
    - "chat/chat.md"      -> prompts/chat/chat.md
    - 其它嵌套路径会被视为 `prompts/` 下的相对路径。
    """
    if _PROMPT_CACHE_READY:
        key = _resolve_prompt_key(name)
        if key not in _PROMPT_CACHE:
            raise FileNotFoundError(f"Prompt '{name}' not found in database cache")
        return _expand_db_includes(key, depth=0)

    path = _prompt_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Prompt '{name}' not found at: {path}")

    text = path.read_text(encoding="utf-8")
    return _expand_includes(text=text, base_dir=path.parent, depth=0)


_INCLUDE_RE = re.compile(r"\{\{\s*include\s*:\s*([^\}]+?)\s*\}\}")


def _normalize_key(name: str) -> str:
    rel = name.strip().replace("\\", "/")
    if rel.endswith(".md"):
        rel = rel[:-3]
    parts: list[str] = []
    for part in PurePosixPath(rel).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ValueError(f"非法 prompt 路径: {name}")
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _resolve_prompt_key(name: str, *, base_key: str | None = None) -> str:
    if base_key:
        raw = name.strip().replace("\\", "/")
        if raw.startswith("/"):
            raise ValueError(f"非法 include 路径: {name}")
        base = PurePosixPath(base_key).parent
        key = _normalize_key(str(base / raw))
    else:
        key = _normalize_key(name)

    if key in _PROMPT_CACHE:
        return key
    if "/" not in key:
        standard_key = f"{key}/{key}"
        if standard_key in _PROMPT_CACHE:
            return standard_key
    return key


def _expand_db_includes(key: str, depth: int) -> str:
    if depth > 5:
        raise ValueError("Prompt include 递归深度超过限制")
    text = _PROMPT_CACHE[key]

    def _replace(m: re.Match) -> str:
        rel = m.group(1).strip()
        inc_key = _resolve_prompt_key(rel, base_key=key)
        if inc_key not in _PROMPT_CACHE:
            raise FileNotFoundError(f"include prompt 不存在: {rel}")
        return _expand_db_includes(inc_key, depth + 1)

    return _INCLUDE_RE.sub(_replace, text)


def load_prompts_from_documents(docs: list[dict[str, Any]]) -> int:
    """Replace runtime prompt cache from MongoDB documents."""
    global _PROMPT_CACHE_READY
    cache: dict[str, str] = {}
    for doc in docs:
        slug = str(doc.get("slug") or doc.get("prompt_id") or "").strip()
        if not slug:
            continue
        content = (
            doc.get("content")
            or doc.get("system_prompt")
            or doc.get("user_prompt_template")
            or ""
        )
        cache[_normalize_key(slug)] = str(content)
    _PROMPT_CACHE.clear()
    _PROMPT_CACHE.update(cache)
    _PROMPT_CACHE_READY = True
    return len(_PROMPT_CACHE)


def _expand_includes(text: str, base_dir: Path, depth: int) -> str:
    if depth > 5:
        raise ValueError("Prompt include 递归深度超过限制")

    def _replace(m: re.Match) -> str:
        rel = m.group(1).strip()
        inc_path = (base_dir / rel).resolve()

        # 安全限制：只允许包含 prompts 目录内文件
        if PROMPTS_DIR not in inc_path.parents and inc_path != PROMPTS_DIR:
            raise ValueError(f"非法 include 路径（必须在 prompts 目录内）：{rel}")
        if inc_path.suffix != ".md":
            raise ValueError(f"include 必须指向 .md 文件：{rel}")
        if not inc_path.exists():
            raise FileNotFoundError(f"include 文件不存在：{rel}")

        inc_text = inc_path.read_text(encoding="utf-8")
        return _expand_includes(text=inc_text, base_dir=inc_path.parent, depth=depth + 1)

    return _INCLUDE_RE.sub(_replace, text)


def list_prompts() -> List[str]:
    """
    列出可用的 prompt 名称。

    规则：
    - 对于形如 `chat/chat.md` 的文件，只返回 `chat`
    - 其它情况返回相对路径（去掉 `.md`，使用 `/` 作为分隔符）
    """
    if _PROMPT_CACHE_READY:
        names = set()
        for key in _PROMPT_CACHE:
            rel = PurePosixPath(key)
            if rel.parent.name == rel.name:
                names.add(rel.name)
            else:
                names.add(key)
        return sorted(names)

    names = set()

    for p in PROMPTS_DIR.rglob("*.md"):
        rel = p.relative_to(PROMPTS_DIR)
        stem = p.stem

        # 标准结构：<name>/<name>.md -> 只暴露 <name>
        if rel.parent.name == stem:
            names.add(stem)
        else:
            # 其它情况，返回相对路径（不含扩展名）
            names.add(str(rel.with_suffix("")).replace(os.sep, "/"))

    return sorted(names)

