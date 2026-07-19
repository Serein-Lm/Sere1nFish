"""Unified notification hooks.

Business code should call this module instead of importing a specific channel
implementation such as DingTalk.  The notification layer owns formatting,
secret redaction, routing policy, channel fan-out, and best-effort failure
handling.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

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
    """Build concise human-facing Markdown; context stays internal for routing/logs."""
    del event, level, source, project_id, task_id, context
    lines = [f"## {title}"]
    if content:
        lines.extend(["", _clip(content)])
    lines.extend(["", f"> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
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
    """Notify only actionable target results or failures."""
    summary = dict(summary or {})
    normalized_status = str(status or "completed")
    status_text = {
        "completed": "完成",
        "partial": "部分完成",
        "failed": "失败",
    }.get(normalized_status, normalized_status)
    target_label = target_name or target_id or "Target"
    if normalized_status in {"completed", "partial"}:
        if not _has_high_value_summary(summary):
            return False
        lines = ["**结论**", f"- 扫描{status_text}，发现高价值结果"]
        modules = [str(item) for item in summary.get("enabled_modules") or [] if item]
        if modules:
            lines.append(f"- 已执行：{'、'.join(modules)}")
        metrics = [
            ("网站高价值发现", summary.get("url_findings", 0)),
            ("招投标高价值发现", summary.get("bidding_findings", 0)),
            ("公众号高分内容", summary.get("wechat_high_score_records", 0)),
            ("公众号联系方式", summary.get("wechat_contacts", 0)),
            ("目标单位已验证学者文章", summary.get("scholar_verified_articles", 0)),
            ("小红书高分画像", summary.get("xhs_profiles", 0)),
        ]
        metric_lines = [
            f"- {label}：{int(value or 0)}"
            for label, value in metrics
            if int(value or 0) > 0
        ]
        lines.extend(
            ["", "**关键数据**", *(
                metric_lines or ["- 本轮未发现可查看数据"]
            )]
        )
        priorities: list[str] = []
        if int(summary.get("url_findings") or 0):
            priorities.append("网站")
        if int(summary.get("bidding_findings") or 0):
            priorities.append("招投标")
        if int(summary.get("wechat_high_score_records") or 0) or int(
            summary.get("wechat_contacts") or 0
        ):
            priorities.append("公众号")
        if int(summary.get("scholar_verified_articles") or 0):
            priorities.append("学者")
        if int(summary.get("xhs_profiles") or 0):
            priorities.append("小红书")
        lines.extend(
            [
                "",
                "**重点**",
                "- " + ("、".join(priorities) if priorities else "本轮暂无高价值结果"),
            ]
        )
        content = "\n".join(lines)
        title = f"{target_label} 发现高价值信息"
    else:
        content = "**结论**\n- 扫描失败"
        if summary.get("error"):
            content += f"\n- 原因：{_clip(str(summary['error']), 300)}"
        title = f"{target_label} 信息收集{status_text}"
    return notify_event_background(
        event="target.collection.completed",
        title=title,
        content=content,
        level="notice" if normalized_status == "completed" else "warning",
        source=source,
        project_id=project_id,
        task_id=task_id,
        context={
            "target_id": target_id,
            "target_name": target_name,
            "status": normalized_status,
            "summary": summary,
        },
    )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


_HIGH_VALUE_METRIC_KEYS = (
    "url_findings",
    "bidding_findings",
    "wechat_high_score_records",
    "wechat_contacts",
    "scholar_verified_articles",
    "xhs_profiles",
)


def _has_high_value_summary(summary: dict[str, Any]) -> bool:
    return any(_count(summary.get(key)) > 0 for key in _HIGH_VALUE_METRIC_KEYS)


_BATCH_METRIC_KEYS = (
    "assets_alive",
    "url_findings",
    "bidding_records",
    "bidding_findings",
    "wechat_documents",
    "wechat_high_score_records",
    "wechat_contacts",
    "scholar_articles",
    "scholar_verified_articles",
    "scholar_contacts",
    "xhs_notes",
    "xhs_profiles",
)


def _company_scan_task_summary(task: dict[str, Any]) -> dict[str, Any]:
    result = _mapping(task.get("result"))
    identity = _mapping(result.get("identity"))
    params = _mapping(task.get("params"))
    assets = _mapping(result.get("assets"))
    url_scan = _mapping(result.get("url_scan"))
    bidding = _mapping(result.get("bidding"))
    wechat = _mapping(result.get("wechat"))
    scholar = _mapping(result.get("scholar"))
    xhs = _mapping(result.get("xhs"))
    metrics = {
        "assets_alive": _count(assets.get("alive")),
        "url_findings": _count(url_scan.get("findings_count")),
        "bidding_records": _count(bidding.get("records_fetched")),
        "bidding_findings": _count(bidding.get("findings_count")),
        "wechat_documents": _count(wechat.get("documents")),
        "wechat_high_score_records": _count(wechat.get("high_score_records")),
        "wechat_contacts": _count(wechat.get("contacts")),
        "scholar_articles": _count(scholar.get("articles_total")),
        "scholar_verified_articles": _count(scholar.get("verified_articles_total")),
        "scholar_contacts": _count(scholar.get("contacts_total")),
        "xhs_notes": _count(xhs.get("notes_count")),
        "xhs_profiles": _count(xhs.get("profiles_count")),
    }
    company_name = str(
        identity.get("normalized_name")
        or result.get("company_name")
        or params.get("company_name")
        or task.get("task_id")
        or "未知目标"
    )
    return {
        "company_name": company_name,
        "status": str(task.get("status") or "unknown"),
        "error": str(task.get("error") or ""),
        "metrics": metrics,
        "params": params,
        "batch_index": _count(task.get("batch_index")),
    }


def _enabled_batch_modules(items: list[dict[str, Any]]) -> list[str]:
    enabled: list[str] = []
    params_list = [_mapping(item.get("params")) for item in items]
    if any(
        params.get("enable_asset_discovery", True)
        or params.get("enable_url_scan", True)
        for params in params_list
    ):
        enabled.append("网站")
    if any(params.get("enable_bidding", True) for params in params_list):
        enabled.append("招投标")
    if any(params.get("enable_wechat", False) for params in params_list):
        enabled.append("公众号")
    if any(params.get("enable_scholar", False) for params in params_list):
        enabled.append("学者")
    if any(params.get("enable_xhs", False) for params in params_list):
        enabled.append("小红书")
    return enabled


def build_company_scan_batch_notification(
    tasks: list[dict[str, Any]],
) -> tuple[str, str, dict[str, Any]]:
    """Build one compact completion notification for a multi-company batch."""
    items = [_company_scan_task_summary(task) for task in tasks]
    items.sort(key=lambda item: item["batch_index"])
    completed = sum(item["status"] == "completed" for item in items)
    failed_items = [item for item in items if item["status"] != "completed"]
    high_value_items = [
        item for item in items if _has_high_value_summary(item["metrics"])
    ]
    totals = {key: 0 for key in _BATCH_METRIC_KEYS}
    for item in items:
        for key in totals:
            totals[key] += item["metrics"][key]

    lines = [
        "**结论**",
        (
            f"- 共 {len(items)} 家：完成 {completed}，"
            f"高价值 {len(high_value_items)}，失败 {len(failed_items)}"
        ),
    ]
    enabled_modules = _enabled_batch_modules(items)
    if enabled_modules:
        lines.append(f"- 已执行：{'、'.join(enabled_modules)}")

    high_value_totals = [
        ("网站高价值发现", totals["url_findings"]),
        ("招投标高价值发现", totals["bidding_findings"]),
        ("公众号高分内容", totals["wechat_high_score_records"]),
        ("公众号联系方式", totals["wechat_contacts"]),
        ("目标单位已验证学者文章", totals["scholar_verified_articles"]),
        ("小红书高分画像", totals["xhs_profiles"]),
    ]
    visible_totals = [
        f"- {label}：{value}" for label, value in high_value_totals if value > 0
    ]
    if visible_totals:
        lines.extend(["", "**高分结果**", *visible_totals])

    ranked: list[tuple[int, int, str]] = []
    for item in high_value_items:
        metrics = item["metrics"]
        score = (
            metrics["url_findings"] * 8
            + metrics["bidding_findings"] * 7
            + metrics["wechat_high_score_records"] * 6
            + metrics["wechat_contacts"] * 8
            + metrics["xhs_profiles"] * 6
            + metrics["scholar_verified_articles"] * 3
        )
        highlights: list[str] = []
        if metrics["url_findings"]:
            highlights.append(f"网站 {metrics['url_findings']}")
        if metrics["bidding_findings"]:
            highlights.append(f"招投标 {metrics['bidding_findings']}")
        if metrics["wechat_high_score_records"] or metrics["wechat_contacts"]:
            highlights.append(
                "公众号高分/联系 "
                f"{metrics['wechat_high_score_records']}/{metrics['wechat_contacts']}"
            )
        if metrics["scholar_verified_articles"]:
            highlights.append(f"学者验证 {metrics['scholar_verified_articles']}")
        if metrics["xhs_profiles"]:
            highlights.append(f"小红书画像 {metrics['xhs_profiles']}")
        if score > 0 and highlights:
            ranked.append(
                (score, -item["batch_index"], f"{item['company_name']}：{'、'.join(highlights)}")
            )
    ranked.sort(reverse=True)
    if ranked:
        lines.extend(["", "**优先查看**"])
        lines.extend(f"- {entry}" for _, _, entry in ranked[:5])

    if failed_items:
        failed_names = [item["company_name"] for item in failed_items]
        visible = failed_names[:5]
        suffix = f" 等 {len(failed_names)} 家" if len(failed_names) > 5 else ""
        lines.extend(["", "**失败目标**", f"- {'、'.join(visible)}{suffix}"])

    title = (
        f"{len(high_value_items)} 家公司发现高价值信息"
        if high_value_items
        else f"{len(failed_items)} 家公司扫描异常"
        if failed_items
        else f"{len(items)} 家公司扫描结束"
    )
    context = {
        "total": len(items),
        "completed": completed,
        "failed": len(failed_items),
        "high_value_companies": len(high_value_items),
        "metrics": totals,
    }
    return title, "\n".join(lines), context


async def notify_company_scan_batch_completed(
    *,
    project_id: str,
    batch_id: str,
    tasks: list[dict[str, Any]],
) -> NotificationDispatchResult:
    title, content, context = build_company_scan_batch_notification(tasks)
    if not context["high_value_companies"] and not context["failed"]:
        return NotificationDispatchResult(
            event="company_scan.batch.completed",
            ok=True,
            results=[],
            skipped=True,
            message="no high-value results",
        )
    return await notify_event(
        event="company_scan.batch.completed",
        title=title,
        content=content,
        level="warning" if context["failed"] else "notice",
        source="project_task_batch",
        project_id=project_id,
        context={"batch_id": batch_id, **context},
    )
