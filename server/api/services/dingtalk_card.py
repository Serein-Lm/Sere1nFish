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
_REFERENCE_MARKER_RE = re.compile(r"\[\[ref:[^|\]]+\|([^\]]+)\]\]")
_RELATIVE_DOWNLOAD_RE = re.compile(
    r"(?m)^\s*下载链接[：:]\s*/api/v1/artifacts/[^\s]+\s*$"
)

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


def clean_hub_markdown(value: Any) -> str:
    """Render web-only entity markers as readable DingTalk Markdown."""
    text = str(value or "").strip()
    text = _ARTIFACT_MARKER_RE.sub(lambda match: f"**产物：{match.group(1)}**", text)
    text = _REFERENCE_MARKER_RE.sub(lambda match: f"**{match.group(1)}**", text)
    text = _RELATIVE_DOWNLOAD_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


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
        self.errors: list[str] = []
        self.tool_count = 0
        self.event_count = 0
        self._synthesis_started = False

    @property
    def live_length(self) -> int:
        return len(self.live_text)

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
            if not chunk:
                return
            if ".synthesize" in path and not self._synthesis_started:
                self.live_text = ""
                self._synthesis_started = True
            self.live_text = (self.live_text + chunk)[-20_000:]

    def render_running(self, *, max_chars: int = 12_000) -> str:
        completed = sum(item.status == "done" for item in self.items)
        failed = sum(item.status == "failed" for item in self.items)
        elapsed = max(0, int(time.monotonic() - self.started_at))
        current = next(
            (item for item in reversed(self.items) if item.status == "running"),
            None,
        )

        lines = [
            "### AI 中枢正在执行",
            "",
            f"**当前阶段**：{current.label if current else '正在整理结果'}",
            f"**执行概况**：已完成 {completed} 个阶段，调用 {self.tool_count} 个工具，耗时 {elapsed} 秒",
        ]
        if current and current.description:
            lines.append(f"**阶段说明**：{current.description}")

        visible = self.items[-8:]
        if visible:
            lines.extend(["", "#### 执行进度"])
            status_labels = {"done": "完成", "failed": "失败", "running": "进行中"}
            for item in visible:
                lines.append(f"- [{status_labels[item.status]}] {item.label}")

        if failed and self.errors:
            lines.extend(["", "#### 异常信息"])
            lines.extend(f"- {message}" for message in self.errors)

        prefix = "\n".join(lines).strip()
        live = clean_hub_markdown(self.live_text)
        if not live:
            live = "等待当前阶段返回可展示内容。"
        available = max(200, max_chars - len(prefix) - 40)
        if len(live) > available:
            live = "…\n" + live[-available + 2 :]
        return f"{prefix}\n\n#### 阶段输出\n{live}"[:max_chars]

    def render_final(
        self,
        final_text: str,
        artifacts: list[dict[str, Any]],
        *,
        base_url: str = "",
        max_chars: int = 12_000,
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

        footer = [
            "---",
            f"> 已完成 {completed} 个阶段，调用 {self.tool_count} 个工具，总耗时 {elapsed} 秒。",
        ]
        if artifacts:
            footer.extend(["", "### 交付产物", *artifact_lines])
            if not base_url:
                footer.extend(
                    ["", "> 已生成产物；管理员配置“公网访问地址”后，钉钉中会显示打开和下载入口。"]
                )
        footer_text = "\n".join(footer)
        header = "### 执行结果\n\n"
        available = max(300, max_chars - len(header) - len(footer_text) - 2)
        if len(answer) > available:
            answer = answer[: max(0, available - 18)].rstrip() + "\n\n…（正文过长已截断）"
        return f"{header}{answer}\n\n{footer_text}"[:max_chars]

    def _latest_running(self, path: str) -> _ProgressItem | None:
        return next(
            (
                item
                for item in reversed(self.items)
                if item.path == path and item.status == "running"
            ),
            None,
        )
