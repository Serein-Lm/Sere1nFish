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
        summary={"assets_alive": 3},
    )
    assert captured["event"] == "target.collection.completed"
    assert captured["title"] == "示例公司 信息收集完成"
    assert captured["context"] == {
        "target_id": "target-1",
        "target_name": "示例公司",
        "status": "completed",
        "summary": {"assets_alive": 3},
    }
