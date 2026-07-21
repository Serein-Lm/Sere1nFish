"""DingTalk AI Card presentation for unified AI Hub events.

This module is deliberately transport-free.  It turns the stable workflow
event contract into user-facing progress Markdown and artifact actions, while
``dingtalk_stream`` remains responsible for SDK calls and connection state.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any
from urllib.parse import quote, urlsplit


_ARTIFACT_MARKER_RE = re.compile(r"\[\[artifact:[^|\]]+\|([^\]]+)\]\]")
_LABELED_REFERENCE_MARKER_RE = re.compile(r"\[\[ref:[^\]]*?\|([^\]]+)\]\]")
_REFERENCE_MARKER_RE = re.compile(r"\[\[ref:[^\]]+\]\]")
_RELATIVE_DOWNLOAD_RE = re.compile(
    r"(?m)^\s*下载链接[：:]\s*/api/v1/artifacts/[^\s]+\s*$"
)
_PROGRESS_LABEL_PREFIX_RE = re.compile(r"^[^\w]+", re.UNICODE)
_INCOMPLETE_MARKERS = ("[[artifact:", "[[ref:")
_LIVE_TEXT_LIMIT = 20_000
_TRUNCATED_SUFFIX = "\n\n…（内容较长，已截断）"

_FORMAT_BY_SUFFIX = {
    ".doc": "Word",
    ".docx": "Word",
    ".pdf": "PDF",
    ".xls": "Excel",
    ".xlsx": "Excel",
    ".csv": "CSV",
    ".ppt": "PowerPoint",
    ".pptx": "PowerPoint",
    ".md": "Markdown",
    ".txt": "文本",
    ".json": "JSON",
    ".png": "图片",
    ".jpg": "图片",
    ".jpeg": "图片",
    ".webp": "图片",
    ".gif": "图片",
    ".mp3": "音频",
    ".wav": "音频",
    ".m4a": "音频",
    ".mp4": "视频",
    ".mov": "视频",
}

_FORMAT_BY_KIND = {
    "word": "Word",
    "payload_word": "载荷 Word",
    "persona_word": "人物 Word",
    "pdf": "PDF",
    "excel": "Excel",
    "spreadsheet": "Excel",
    "markdown": "Markdown",
    "text": "文本",
    "json": "JSON",
    "image": "图片",
    "audio": "音频",
    "video": "视频",
}


def _clean_text(value: Any, *, limit: int = 300) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _clean_progress_label(value: Any) -> str:
    """Remove decorative glyphs from compact Card progress labels."""
    text = _clean_text(value, limit=80)
    return _PROGRESS_LABEL_PREFIX_RE.sub("", text).strip() or "执行阶段"


def clean_hub_markdown(value: Any) -> str:
    """Render web-only entity markers as readable DingTalk Markdown."""
    text = str(value or "").strip()
    marker_start = max(text.rfind(marker) for marker in _INCOMPLETE_MARKERS)
    if marker_start >= 0 and "]]" not in text[marker_start:]:
        text = text[:marker_start].rstrip()
    text = re.sub(
        r"(?m)^\*\*([^*\n]{1,40})\*\*[ \t]*$",
        r"#### \1",
        text,
    )
    text = _ARTIFACT_MARKER_RE.sub(lambda match: f"**产物：{match.group(1)}**", text)
    text = _LABELED_REFERENCE_MARKER_RE.sub(lambda match: match.group(1), text)
    text = _REFERENCE_MARKER_RE.sub("", text)
    text = _RELATIVE_DOWNLOAD_RE.sub("", text)
    text = re.sub(r"(?m)^([ \t]*[-+*])\s+", r"\1 ", text)
    text = re.sub(r"(?m)^([ \t]*\d+[.)])\s+", r"\1 ", text)
    text = re.sub(r"[ \t]+([，。；：:,.!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _fit_stable_prefix(text: str, available: int) -> str:
    """Bound live content without sliding the visible window on every update."""
    if len(text) <= available:
        return text
    keep = max(0, available - len(_TRUNCATED_SUFFIX))
    return text[:keep].rstrip() + _TRUNCATED_SUFFIX


def artifact_format(artifact: dict[str, Any]) -> str:
    """Return a stable user-facing format label for current and future artifacts."""
    kind = str(artifact.get("kind") or "").strip().lower()
    if kind in _FORMAT_BY_KIND:
        return _FORMAT_BY_KIND[kind]
    suffix = PurePath(str(artifact.get("filename") or "")).suffix.lower()
    return _FORMAT_BY_SUFFIX.get(suffix, "文件")


def artifact_open_url(base_url: str, artifact_id: Any) -> str:
    base = str(base_url or "").strip().rstrip("/")
    identifier = str(artifact_id or "").strip()
    if not base or not identifier:
        return ""
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{base}/phishing?ref_artifact={quote(identifier, safe='')}"


def build_artifact_buttons(
    artifacts: list[dict[str, Any]],
    *,
    base_url: str,
    limit: int = 5,
) -> list[dict[str, str]]:
    """Build AI Card buttons without leaking the authenticated storage URL."""
    buttons: list[dict[str, str]] = []
    for artifact in artifacts:
        if len(buttons) >= max(0, limit):
            break
        url = artifact_open_url(base_url, artifact.get("artifact_id"))
        if not url or not artifact.get("download_url"):
            continue
        title = _clean_text(artifact.get("title") or artifact.get("filename") or "产物", limit=18)
        label = f"打开/下载 {artifact_format(artifact)} · {title}"
        buttons.append({"text": label[:40], "url": url, "color": "blue"})
    return buttons


def _format_size(size: Any) -> str:
    try:
        value = max(0, int(size or 0))
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


@dataclass
class _ProgressItem:
    path: str
    label: str
    node_type: str
    status: str = "running"
    description: str = ""


class DingTalkCardRenderer:
    """Accumulate workflow events into bounded running and final card views."""

    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self.items: list[_ProgressItem] = []
        self.live_text = ""
        self.live_truncated = False
        self.errors: list[str] = []
        self.tool_count = 0
        self.event_count = 0
        self._answer_started = False

    @property
    def live_length(self) -> int:
        return len(self.live_text)

    @property
    def answer_started(self) -> bool:
        return self._answer_started

    def consume(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        path = str(event.get("path") or "graph")
        self.event_count += 1

        if event_type == "start":
            node_type = str(data.get("type") or "stage")
            label = _clean_text(
                data.get("displayName") or data.get("name") or node_type,
                limit=80,
            )
            self.items.append(
                _ProgressItem(
                    path=path,
                    label=label or "执行阶段",
                    node_type=node_type,
                    description=_clean_text(data.get("description"), limit=160),
                )
            )
            self.items = self.items[-100:]
            if node_type == "tool":
                self.tool_count += 1
            return

        if event_type == "update":
            item = self._latest_running(path)
            if item:
                item.description = _clean_text(
                    data.get("description") or data.get("status"), limit=160
                )
            return

        if event_type == "end":
            item = self._latest_running(path)
            if item:
                item.status = "failed" if data.get("status") == "error" else "done"
            return

        if event_type == "error":
            message = _clean_text(data.get("error") or "执行失败", limit=300)
            if message:
                self.errors.append(message)
                self.errors = self.errors[-3:]
            item = self._latest_running(path)
            if item:
                item.status = "failed"
                item.description = message
            return

        if event_type == "content":
            chunk = str(data.get("content") or "")
            if not chunk or not self._is_answer_path(path):
                return
            self._answer_started = True
            if len(self.live_text) >= _LIVE_TEXT_LIMIT:
                self.live_truncated = True
                return
            remaining = _LIVE_TEXT_LIMIT - len(self.live_text)
            self.live_text += chunk[:remaining]
            self.live_truncated = len(chunk) > remaining

    def render_running(self, *, max_chars: int = 12_000) -> str:
        completed = sum(item.status == "done" for item in self.items)
        elapsed = max(0, int(time.monotonic() - self.started_at))
        current = next(
            (item for item in reversed(self.items) if item.status == "running"),
            None,
        )

        lines = [
            "### 正在处理",
            "",
            f"**当前阶段**：{current.label if current else '正在整理关键结果'}",
            f"> 已完成 {completed} 个阶段 · 调用 {self.tool_count} 个工具 · {elapsed} 秒",
        ]
        if current and current.description:
            lines.append(_clean_text(current.description, limit=100))
        return "\n".join(lines).strip()[:max_chars]

    def render_streaming(self, *, max_chars: int = 12_000) -> str:
        """Render only the stable answer surface used for live Card updates."""
        answer = clean_hub_markdown(self.live_text)
        if not answer:
            return ""
        answer = _fit_stable_prefix(answer, max(300, max_chars))
        return answer[:max_chars].rstrip("\n")

    def render_preparations(
        self,
        *,
        final: bool = False,
        max_items: int = 1,
    ) -> list[dict[str, Any]]:
        """Render one compact status row for the Card's progress surface."""
        if final or max_items <= 0:
            return []

        stages = [item for item in self.items if item.node_type != "tool"]
        if not stages:
            return [{"name": "正在处理 · 理解需求", "progress": 0}]

        current = next(
            (
                item
                for item in reversed(stages)
                if item.status == "running" and item.path != "graph"
            ),
            None,
        )
        if current is not None:
            return [
                {
                    "name": f"正在执行 · {_clean_progress_label(current.label)}",
                    "progress": 50,
                }
            ]

        if any(item.status == "failed" for item in stages):
            return [{"name": "部分阶段异常 · 正在整理结果", "progress": 90}]

        return [{"name": "正在整理关键结果", "progress": 90}]

    def render_final(
        self,
        final_text: str,
        artifacts: list[dict[str, Any]],
        *,
        base_url: str = "",
        max_chars: int = 12_000,
        include_execution_summary: bool = True,
    ) -> str:
        answer = clean_hub_markdown(final_text) or "（本次未生成文本内容）"
        elapsed = max(0, int(time.monotonic() - self.started_at))
        completed = sum(item.status == "done" for item in self.items)

        artifact_lines: list[str] = []
        visible_artifacts = artifacts[:8]
        for index, artifact in enumerate(visible_artifacts, start=1):
            title = _clean_text(
                artifact.get("title") or artifact.get("filename") or "未命名产物",
                limit=80,
            )
            details = [artifact_format(artifact)]
            size = _format_size(artifact.get("size"))
            if size:
                details.append(size)
            url = artifact_open_url(base_url, artifact.get("artifact_id"))
            action = f"，[打开并下载]({url})" if url else ""
            artifact_lines.append(
                f"{index}. **{title}**（{' / '.join(details)}）{action}"
            )
        if len(artifacts) > len(visible_artifacts):
            artifact_lines.append(f"另有 {len(artifacts) - len(visible_artifacts)} 个产物，请在 AI 中枢查看。")

        footer: list[str] = []
        if include_execution_summary:
            summary_parts = [f"{completed} 个阶段", f"{self.tool_count} 个工具", f"{elapsed} 秒"]
            footer.extend(["---", f"> 执行摘要：{' · '.join(summary_parts)}"])
            if self.errors:
                footer.append(f"> 部分步骤异常：{self.errors[-1]}")
        if artifacts:
            if footer:
                footer.append("")
            footer.extend(["### 产物", *artifact_lines])
            if not base_url:
                footer.extend(
                    ["", "> 已生成产物；管理员配置“公网访问地址”后，钉钉中会显示打开和下载入口。"]
                )
        footer_text = "\n".join(footer)
        footer_spacing = 2 if footer_text else 0
        available = max(300, max_chars - len(footer_text) - footer_spacing)
        answer = _fit_stable_prefix(answer, available)
        suffix = f"\n\n{footer_text}" if footer_text else ""
        return f"{answer}{suffix}"[:max_chars]

    def _latest_running(self, path: str) -> _ProgressItem | None:
        return next(
            (
                item
                for item in reversed(self.items)
                if item.path == path and item.status == "running"
            ),
            None,
        )

    @staticmethod
    def _is_answer_path(path: str) -> bool:
        """Keep specialist reasoning out of the Card's primary answer surface."""
        return (
            ".synthesize" in path
            or ".finalize" in path
            or path.startswith("graph.agents.")
        )
