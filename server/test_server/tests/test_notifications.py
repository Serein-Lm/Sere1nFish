from __future__ import annotations

from typing import Any

import pytest


def test_dingtalk_webhook_without_secret_uses_unsigned_url() -> None:
    from crawler_tools.dingtalk_bot import DingTalkBot

    bot = DingTalkBot(access_token="token-value", secret="", keyword="通知")

    assert bot._build_url() == (
        "https://oapi.dingtalk.com/robot/send?access_token=token-value"
    )


@pytest.mark.asyncio
async def test_dingtalk_factory_accepts_keyword_security_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from crawler_tools import dingtalk_bot as module

    async def _config(_bot_name: str = "default") -> module.DingTalkConfig:
        return module.DingTalkConfig(
            name="default",
            access_token="token-value",
            secret="",
            keyword="通知",
        )

    monkeypatch.setattr(module, "get_dingtalk_config_from_db", _config)

    assert await module.create_dingtalk_bot("default") is not None


@pytest.mark.asyncio
async def test_default_notification_uses_first_enabled_webhook_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import config as config_dao
    from api.db import mongodb
    from api.services import notifications
    from crawler_tools import dingtalk_bot

    class _Bot:
        async def send_markdown(self, *_args: Any, **_kwargs: Any) -> dingtalk_bot.SendResult:
            return dingtalk_bot.SendResult(success=True, message="ok", errcode=0)

    async def _create(name: str) -> Any:
        return _Bot() if name == "notify" else None

    async def _configs(_db: Any) -> dict[str, dict[str, Any]]:
        return {
            "disabled": {"enabled": False, "access_token": "x"},
            "notify": {"enabled": True, "access_token": "y", "secret": ""},
        }

    monkeypatch.setattr(dingtalk_bot, "create_dingtalk_bot", _create)
    monkeypatch.setattr(config_dao, "list_dingtalk_configs", _configs)
    monkeypatch.setattr(mongodb, "get_db", lambda: object())

    result = await notifications._send_dingtalk(
        title="完成",
        markdown="内容",
        text="内容",
        msg_type="markdown",
        bot_name="default",
        at_mobiles=[],
        at_all=False,
    )

    assert result.success is True
    assert result.target == "notify"


@pytest.mark.asyncio
async def test_unconfigured_default_webhook_is_a_clean_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dao import config as config_dao
    from api.db import mongodb
    from api.services import notifications
    from crawler_tools import dingtalk_bot

    async def _create(_name: str) -> None:
        return None

    async def _configs(_db: Any) -> dict[str, dict[str, Any]]:
        return {}

    monkeypatch.setattr(dingtalk_bot, "create_dingtalk_bot", _create)
    monkeypatch.setattr(config_dao, "list_dingtalk_configs", _configs)
    monkeypatch.setattr(mongodb, "get_db", lambda: object())

    result = await notifications._send_dingtalk(
        title="完成",
        markdown="内容",
        text="内容",
        msg_type="markdown",
        bot_name="default",
        at_mobiles=[],
        at_all=False,
    )

    assert result.success is False
    assert result.skipped is True


def test_target_completion_notification_uses_unified_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import notifications

    captured: dict[str, Any] = {}

    def _notify(**kwargs: Any) -> bool:
        captured.update(kwargs)
        return True

    monkeypatch.setattr(notifications, "notify_event_background", _notify)

    assert notifications.notify_target_collection_completed(
        project_id="project-1",
        task_id="task-1",
        target_id="target-1",
        target_name="示例公司",
        source="company_scan_pipeline",
        summary={"url_findings": 3},
    )
    assert captured["event"] == "target.collection.completed"
    assert captured["title"] == "示例公司 发现高价值信息"
    assert "网站高价值发现：3" in captured["content"]
    assert "**重点**" in captured["content"]
    assert "- 网站" in captured["content"]
    assert captured["context"] == {
        "target_id": "target-1",
        "target_name": "示例公司",
        "status": "completed",
        "summary": {"url_findings": 3},
    }


def test_target_completion_without_high_value_does_not_notify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import notifications

    monkeypatch.setattr(
        notifications,
        "notify_event_background",
        lambda **_kwargs: pytest.fail("低价值完成不应发送通知"),
    )
    assert notifications.notify_target_collection_completed(
        project_id="project-1",
        task_id="task-1",
        target_id="target-1",
        target_name="示例公司",
        source="company_scan_pipeline",
        summary={"assets_alive": 30, "bidding_records": 10},
    ) is False


def test_human_notification_markdown_hides_internal_context() -> None:
    from api.services.notifications import format_notification_markdown

    markdown = format_notification_markdown(
        event="task.done",
        title="扫描完成",
        content="关键结果",
        level="notice",
        source="runtime",
        project_id="project-1",
        task_id="task-1",
        context={"result": {"raw": "不应展示"}},
    )

    assert "扫描完成" in markdown
    assert "关键结果" in markdown
    assert "```json" not in markdown
    assert "不应展示" not in markdown
    assert "Event:" not in markdown
    assert "project-1" not in markdown
    assert "task-1" not in markdown


@pytest.mark.asyncio
async def test_multi_company_completion_sends_one_compact_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import notifications

    captured: list[dict[str, Any]] = []

    async def _notify(**kwargs: Any) -> notifications.NotificationDispatchResult:
        captured.append(kwargs)
        return notifications.NotificationDispatchResult(
            event=str(kwargs["event"]),
            ok=True,
            results=[],
        )

    monkeypatch.setattr(notifications, "notify_event", _notify)
    tasks = [
        {
            "task_id": "task-1",
            "batch_index": 1,
            "status": "completed",
            "params": {
                "company_name": "安徽广播电视台",
                "enable_wechat": True,
                "enable_scholar": True,
            },
            "result": {
                "company_name": "安徽广播电视台",
                "assets": {"alive": 12},
                "url_scan": {"findings_count": 4},
                "bidding": {"records_fetched": 3, "findings_count": 1},
                "wechat": {"documents": 2, "contacts": 1},
                "scholar": {
                    "articles_total": 8,
                    "verified_articles_total": 2,
                    "contacts_total": 0,
                },
            },
        },
        {
            "task_id": "task-2",
            "batch_index": 2,
            "status": "error",
            "error": "超时",
            "params": {
                "company_name": "鞍钢集团有限公司",
                "enable_wechat": True,
                "enable_scholar": True,
            },
        },
    ]

    result = await notifications.notify_company_scan_batch_completed(
        project_id="project-1",
        batch_id="batch-1",
        tasks=tasks,
    )

    assert result.ok is True
    assert len(captured) == 1
    assert captured[0]["title"] == "1 家公司发现高价值信息"
    assert "共 2 家：完成 1，高价值 1，失败 1" in captured[0]["content"]
    assert "目标单位已验证学者文章：2" in captured[0]["content"]
    assert "安徽广播电视台" in captured[0]["content"]
    assert "失败目标" in captured[0]["content"]
    assert "鞍钢集团有限公司" in captured[0]["content"]
    assert "```json" not in captured[0]["content"]


@pytest.mark.asyncio
async def test_multi_company_completion_skips_batch_without_high_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.services import notifications

    async def _notify(**_kwargs: Any) -> notifications.NotificationDispatchResult:
        pytest.fail("无高价值且无异常的批次不应发送通知")

    monkeypatch.setattr(notifications, "notify_event", _notify)
    result = await notifications.notify_company_scan_batch_completed(
        project_id="project-1",
        batch_id="batch-1",
        tasks=[
            {
                "task_id": "task-1",
                "batch_index": 1,
                "status": "completed",
                "params": {"company_name": "普通公司"},
                "result": {
                    "assets": {"alive": 20},
                    "bidding": {"records_fetched": 12},
                },
            }
        ],
    )

    assert result.ok is True
    assert result.skipped is True
