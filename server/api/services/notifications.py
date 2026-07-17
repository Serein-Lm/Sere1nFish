"""Unified notification hooks.

Business code should call this module instead of importing a specific channel
implementation such as DingTalk.  The notification layer owns formatting,
secret redaction, routing policy, channel fan-out, and best-effort failure
handling.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

from api.utils.config_crypto import is_sensitive_key
from core.logger import get_logger
from core.background import spawn_background


NotificationLevel = Literal["debug", "info", "notice", "warning", "error", "critical"]

DEFAULT_CHANNELS = ["dingtalk"]
DEFAULT_BOT_NAME = "default"
SUPPORTED_CHANNELS = {"dingtalk"}

logger = get_logger("api.services.notifications")


@dataclass
class NotificationResult:
    channel: str
    target: str
    success: bool
    message: str
    errcode: int | None = None
    skipped: bool = False


@dataclass
class NotificationDispatchResult:
    event: str
    ok: bool
    results: list[NotificationResult]
    skipped: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "ok": self.ok,
            "skipped": self.skipped,
            "message": self.message,
            "results": [asdict(item) for item in self.results],
        }


def _redact(value: Any, *, parent_key: str = "") -> Any:
    if parent_key and is_sensitive_key(parent_key):
        return "***"
    if isinstance(value, dict):
        return {str(key): _redact(child, parent_key=str(key)) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact(item, parent_key=parent_key) for item in value]
    return value


def _clip(text: str, limit: int = 8000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n...(truncated)"


def _as_list(value: Any, default: list[str]) -> list[str]:
    if value is None or value == "":
        return list(default)
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return list(default)


async def _load_notification_config() -> dict[str, Any]:
    try:
        from api.services.runtime_config import get_runtime_config_section

        config = await get_runtime_config_section("notifications")
        return config if isinstance(config, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"通知配置读取失败，使用默认策略: {exc}")
        return {}


def _event_policy(config: dict[str, Any], event: str) -> dict[str, Any]:
    events = config.get("events", {})
    if not isinstance(events, dict):
        return {}
    policy = events.get(event, {})
    return policy if isinstance(policy, dict) else {}


def _resolve_channels(
    config: dict[str, Any],
    policy: dict[str, Any],
    channels: list[str] | None,
) -> list[str]:
    raw = (
        channels
        or policy.get("channels")
        or config.get("channels")
        or config.get("default_channels")
        or DEFAULT_CHANNELS
    )
    return [channel for channel in _as_list(raw, DEFAULT_CHANNELS) if channel in SUPPORTED_CHANNELS]


def _resolve_dingtalk_options(
    config: dict[str, Any],
    policy: dict[str, Any],
    *,
    bot_name: str | None,
    at_mobiles: list[str] | None,
    at_all: bool | None,
) -> dict[str, Any]:
    global_options = config.get("dingtalk", {})
    event_options = policy.get("dingtalk", {})
    if not isinstance(global_options, dict):
        global_options = {}
    if not isinstance(event_options, dict):
        event_options = {}

    return {
        "bot_name": (
            bot_name
            or event_options.get("bot_name")
            or policy.get("bot_name")
            or global_options.get("bot_name")
            or DEFAULT_BOT_NAME
        ),
        "at_mobiles": at_mobiles
        if at_mobiles is not None
        else _as_list(event_options.get("at_mobiles") or global_options.get("at_mobiles"), []),
        "at_all": bool(
            at_all
            if at_all is not None
            else event_options.get("at_all", global_options.get("at_all", False))
        ),
    }


def _should_dispatch(
    config: dict[str, Any],
    policy: dict[str, Any],
    *,
    level: str,
    force: bool,
) -> tuple[bool, str]:
    if force:
        return True, ""
    if config.get("enabled", True) is False:
        return False, "notifications disabled"
    if policy.get("enabled") is False:
        return False, "event disabled"
    levels = policy.get("levels") or config.get("levels")
    if levels and str(level) not in {str(item) for item in _as_list(levels, [])}:
        return False, f"level {level} not enabled"
    return True, ""


def format_notification_markdown(
    *,
    event: str,
    title: str,
    content: str = "",
    level: str = "info",
    source: str = "",
    project_id: str | None = None,
    task_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    """Build a consistent Markdown payload for all notification channels."""
    lines = [
        f"## {title}",
        "",
        f"- Event: `{event}`",
        f"- Level: `{level}`",
        f"- Time: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
    ]
    if source:
        lines.append(f"- Source: `{source}`")
    if project_id:
        lines.append(f"- Project: `{project_id}`")
    if task_id:
        lines.append(f"- Task: `{task_id}`")
    if content:
        lines.extend(["", _clip(content)])
    if context:
        redacted = _redact(context)
        lines.extend(
            [
                "",
                "```json",
                _clip(json.dumps(redacted, ensure_ascii=False, indent=2), 4000),
                "```",
            ]
        )
    return "\n".join(lines)


async def _send_dingtalk(
    *,
    title: str,
    markdown: str,
    text: str,
    msg_type: str,
    bot_name: str,
    at_mobiles: list[str],
    at_all: bool,
) -> NotificationResult:
    try:
        from crawler_tools.dingtalk_bot import create_dingtalk_bot

        bot = await create_dingtalk_bot(bot_name)
        resolved_bot_name = bot_name
        if not bot and bot_name == DEFAULT_BOT_NAME:
            from api.dao import config as config_dao
            from api.db.mongodb import get_db

            configs = await config_dao.list_dingtalk_configs(get_db())
            for candidate_name, candidate_config in sorted(configs.items()):
                if not (
                    candidate_config.get("enabled", True)
                    and candidate_config.get("access_token")
                ):
                    continue
                bot = await create_dingtalk_bot(candidate_name)
                if bot:
                    resolved_bot_name = candidate_name
                    break
        if not bot:
            return NotificationResult(
                channel="dingtalk",
                target=bot_name,
                success=False,
                message=f"钉钉机器人 {bot_name} 未配置或未启用",
                skipped=True,
            )
        if msg_type == "text":
            result = await bot.send_text(text, at_mobiles=at_mobiles, at_all=at_all)
        else:
            result = await bot.send_markdown(
                title,
                markdown,
                at_mobiles=at_mobiles,
                at_all=at_all,
            )
        return NotificationResult(
            channel="dingtalk",
            target=resolved_bot_name,
            success=result.success,
            message=result.message,
            errcode=result.errcode,
        )
    except Exception as exc:  # noqa: BLE001
        return NotificationResult(
            channel="dingtalk",
            target=bot_name,
            success=False,
            message=str(exc),
        )


async def notify_event(
    *,
    event: str,
    title: str,
    content: str = "",
    level: NotificationLevel | str = "info",
    source: str = "",
    project_id: str | None = None,
    task_id: str | None = None,
    context: dict[str, Any] | None = None,
    channels: list[str] | None = None,
    bot_name: str | None = None,
    at_mobiles: list[str] | None = None,
    at_all: bool | None = None,
    msg_type: Literal["markdown", "text"] = "markdown",
    force: bool = False,
) -> NotificationDispatchResult:
    """Dispatch one business notification through configured channels.

    This is the canonical hook for business code.  It never raises for channel
    failures; callers can inspect the returned result when they need strict
    handling.
    """
    config = await _load_notification_config()
    policy = _event_policy(config, event)
    should_send, skip_reason = _should_dispatch(
        config,
        policy,
        level=str(level),
        force=force,
    )
    if not should_send:
        return NotificationDispatchResult(
            event=event,
            ok=True,
            skipped=True,
            message=skip_reason,
            results=[],
        )

    resolved_channels = _resolve_channels(config, policy, channels)
    if not resolved_channels:
        return NotificationDispatchResult(
            event=event,
            ok=True,
            skipped=True,
            message="no supported notification channels",
            results=[],
        )

    markdown = format_notification_markdown(
        event=event,
        title=title,
        content=content,
        level=str(level),
        source=source,
        project_id=project_id,
        task_id=task_id,
        context=context,
    )
    text = _clip(f"{title}\n{content}".strip())

    results: list[NotificationResult] = []
    for channel in resolved_channels:
        if channel == "dingtalk":
            options = _resolve_dingtalk_options(
                config,
                policy,
                bot_name=bot_name,
                at_mobiles=at_mobiles,
                at_all=at_all,
            )
            results.append(
                await _send_dingtalk(
                    title=title,
                    markdown=markdown,
                    text=text,
                    msg_type=msg_type,
                    bot_name=str(options["bot_name"]),
                    at_mobiles=options["at_mobiles"],
                    at_all=options["at_all"],
                )
            )

    ok = bool(results) and all(item.success or item.skipped for item in results)
    return NotificationDispatchResult(event=event, ok=ok, results=results)


def notify_event_background(**kwargs: Any) -> bool:
    """Fire-and-forget notification hook for async request/task contexts."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False

    async def _dispatch() -> None:
        result = await notify_event(**kwargs)
        if not result.ok and not result.skipped:
            logger.warning("通知发送失败: %s", result.to_dict())

    event = str(kwargs.get("event") or "event")
    spawn_background(_dispatch(), name=f"notification:{event}")
    return True


def notify_target_collection_completed(
    *,
    project_id: str,
    task_id: str,
    target_id: str,
    target_name: str,
    source: str,
    summary: dict[str, Any] | None = None,
    status: str = "completed",
) -> bool:
    """统一发送一次 Target 采集完成事件，具体通道由通知策略选择。"""
    normalized_status = str(status or "completed")
    status_text = {
        "completed": "完成",
        "partial": "部分完成",
        "failed": "失败",
    }.get(normalized_status, normalized_status)
    target_label = target_name or target_id or "Target"
    return notify_event_background(
        event="target.collection.completed",
        title=f"{target_label} 信息收集{status_text}",
        content=(
            "目标公司本轮信息收集已结束，可在项目中查看最终产物。"
            if normalized_status == "completed"
            else "目标公司本轮信息收集已结束，请结合通知摘要检查异常。"
        ),
        level="notice" if normalized_status == "completed" else "warning",
        source=source,
        project_id=project_id,
        task_id=task_id,
        context={
            "target_id": target_id,
            "target_name": target_name,
            "status": normalized_status,
            "summary": summary or {},
        },
    )
