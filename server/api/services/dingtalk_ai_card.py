"""Unified DingTalk AI Card transport adapter.

The Stream message handler depends on this small session interface instead of
SDK-specific card classes. Custom templates and the SDK fallback therefore
share the same lifecycle and can evolve independently from AI Hub workflows.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from core.logger import get_logger


logger = get_logger("api.services.dingtalk_ai_card")

_CONTENT_KEY = "content"
_CARD_UPDATE_OPTIONS = {
    "updateCardDataByKey": True,
    "updatePrivateDataByKey": True,
}


class _SDKErrorCapture:
    """Turn DingTalk SDK log-only HTTP failures into adapter exceptions."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._last_error = ""

    def reset(self) -> None:
        self._last_error = ""

    def raise_if_error(self, operation: str) -> None:
        if not self._last_error:
            return
        detail = " ".join(self._last_error.split())[:1_000]
        self._last_error = ""
        raise RuntimeError(f"{operation}失败：{detail}")

    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._last_error = str(message or "钉钉 SDK 请求失败")
        delegate_error = getattr(self._delegate, "error", None)
        if callable(delegate_error):
            delegate_error(message, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def stringify_card_data(values: dict[str, Any]) -> dict[str, str]:
    """Convert top-level card variables to the string form required by DingTalk."""
    return {
        key: value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        for key, value in values.items()
    }


class DingTalkCardSession(Protocol):
    """Stable lifecycle exposed to the DingTalk Stream adapter."""

    has_progress_panel: bool

    async def update_progress(self, preparations: list[dict[str, Any]]) -> None: ...

    async def stream(self, markdown: str) -> None: ...

    async def finish(
        self,
        markdown: str,
        *,
        buttons: list[dict[str, Any]],
    ) -> None: ...

    async def fail(self, message: str) -> None: ...


class _TemplateCardSession:
    """Card session for the official content/preparations template schema."""

    has_progress_panel = True

    def __init__(
        self,
        replier: Any,
        card_instance_id: str,
        sdk_errors: _SDKErrorCapture,
    ) -> None:
        self._replier = replier
        self._card_instance_id = card_instance_id
        self._sdk_errors = sdk_errors

    async def _checked(self, operation: str, awaitable: Any) -> Any:
        self._sdk_errors.reset()
        result = await awaitable
        self._sdk_errors.raise_if_error(operation)
        return result

    async def begin(self) -> None:
        await self._checked(
            "初始化钉钉 AI Card",
            self._replier.async_streaming(
                self._card_instance_id,
                content_key=_CONTENT_KEY,
                content_value="",
                append=False,
                finished=False,
                failed=False,
            ),
        )

    async def update_progress(self, preparations: list[dict[str, Any]]) -> None:
        await self._checked(
            "更新钉钉 AI Card 进度",
            self._replier.async_put_card_data(
                self._card_instance_id,
                card_data=stringify_card_data({"preparations": preparations}),
                cardUpdateOptions=_CARD_UPDATE_OPTIONS,
            ),
        )

    async def stream(self, markdown: str) -> None:
        await self._checked(
            "流式更新钉钉 AI Card",
            self._replier.async_streaming(
                self._card_instance_id,
                content_key=_CONTENT_KEY,
                content_value=markdown.rstrip("\n"),
                append=False,
                finished=False,
                failed=False,
            ),
        )

    async def finish(
        self,
        markdown: str,
        *,
        buttons: list[dict[str, Any]],
    ) -> None:
        del buttons  # Artifact links are already rendered in the Markdown body.
        await self._checked(
            "结束钉钉 AI Card",
            self._replier.async_streaming(
                self._card_instance_id,
                content_key=_CONTENT_KEY,
                content_value=markdown.rstrip("\n"),
                append=False,
                finished=True,
                failed=False,
            ),
        )

    async def fail(self, message: str) -> None:
        await self._checked(
            "标记钉钉 AI Card 失败",
            self._replier.async_streaming(
                self._card_instance_id,
                content_key=_CONTENT_KEY,
                content_value=message.rstrip("\n"),
                append=False,
                finished=False,
                failed=True,
            ),
        )


class _LegacyMarkdownCardSession:
    """Compatibility adapter for the SDK's built-in Markdown AI Card."""

    has_progress_panel = False

    def __init__(self, card: Any) -> None:
        self._card = card

    async def update_progress(self, preparations: list[dict[str, Any]]) -> None:
        del preparations

    async def stream(self, markdown: str) -> None:
        await asyncio.to_thread(self._card.ai_streaming, markdown.rstrip("\n"), False)

    async def finish(
        self,
        markdown: str,
        *,
        buttons: list[dict[str, Any]],
    ) -> None:
        await asyncio.to_thread(
            lambda: self._card.ai_finish(markdown=markdown, button_list=buttons)
        )

    async def fail(self, message: str) -> None:
        ai_fail = getattr(self._card, "ai_fail", None)
        if callable(ai_fail):
            await asyncio.to_thread(ai_fail, message)
            return
        await self.finish(message, buttons=[])


async def create_ai_card_session(
    handler: Any,
    incoming: Any,
    *,
    query: str,
    template_id: str = "",
    sdk: Any | None = None,
) -> DingTalkCardSession | None:
    """Create a custom-template card, falling back to the SDK Markdown card."""
    if sdk is None:
        import dingtalk_stream as sdk

    normalized_template_id = str(template_id or "").strip()
    if normalized_template_id:
        try:
            replier = sdk.AICardReplier(handler.dingtalk_client, incoming)
            sdk_errors = _SDKErrorCapture(getattr(replier, "logger", logger))
            replier.logger = sdk_errors
            card_data = stringify_card_data(
                {
                    _CONTENT_KEY: "",
                    "query": str(query or "")[:2_000],
                    "preparations": [{"name": "正在理解需求", "progress": 0}],
                    "charts": [],
                    "config": {"autoLayout": True},
                }
            )
            sdk_errors.reset()
            card_instance_id = await replier.async_create_and_deliver_card(
                normalized_template_id,
                card_data,
            )
            sdk_errors.raise_if_error("创建钉钉 AI Card")
            if card_instance_id:
                session = _TemplateCardSession(
                    replier,
                    str(card_instance_id),
                    sdk_errors,
                )
                await session.begin()
                return session
            logger.warning("自定义钉钉 AI Card 未返回实例 ID，回退内置模板")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"创建自定义钉钉 AI Card 失败，回退内置模板: {exc}")

    try:
        card = await asyncio.to_thread(
            handler.ai_markdown_card_start,
            incoming,
            "AI 中枢",
        )
        if getattr(card, "card_instance_id", None):
            return _LegacyMarkdownCardSession(card)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"创建内置钉钉 AI Card 失败: {exc}")
    return None
