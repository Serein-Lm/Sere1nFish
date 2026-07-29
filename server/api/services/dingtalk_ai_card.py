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
_CARD_REQUEST_TIMEOUT_SECONDS = 10.0
_CARD_BUFFER_CLOSE_TIMEOUT_SECONDS = 12.0


async def _await_card_request(operation: str, awaitable: Any) -> Any:
    try:
        return await asyncio.wait_for(
            awaitable,
            timeout=_CARD_REQUEST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"{operation}超过 {_CARD_REQUEST_TIMEOUT_SECONDS:g} 秒"
        ) from exc


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
        result = await _await_card_request(operation, awaitable)
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
        await _await_card_request(
            "流式更新钉钉 AI Card",
            asyncio.to_thread(self._card.ai_streaming, markdown.rstrip("\n"), False),
        )

    async def finish(
        self,
        markdown: str,
        *,
        buttons: list[dict[str, Any]],
    ) -> None:
        await _await_card_request(
            "结束钉钉 AI Card",
            asyncio.to_thread(
                lambda: self._card.ai_finish(markdown=markdown, button_list=buttons)
            ),
        )

    async def fail(self, message: str) -> None:
        ai_fail = getattr(self._card, "ai_fail", None)
        if callable(ai_fail):
            await _await_card_request(
                "标记钉钉 AI Card 失败",
                asyncio.to_thread(ai_fail, message),
            )
            return
        await self.finish(message, buttons=[])


class BufferedDingTalkCardSession:
    """Coalesce Card updates so DingTalk latency cannot block model streaming."""

    def __init__(self, session: DingTalkCardSession) -> None:
        self._session = session
        self.has_progress_panel = session.has_progress_panel
        self._event = asyncio.Event()
        self._pending_progress: list[dict[str, Any]] | None = None
        self._pending_markdown: str | None = None
        self._closing = False
        self._closed = False
        self._last_error = ""
        # This worker is owned and awaited by the session. Keeping it outside the
        # global fire-and-forget registry lets a cancelled message still flush a
        # terminal Card state during application shutdown.
        self._worker = asyncio.create_task(
            self._run(),
            name="dingtalk_card_updates",
        )

    @property
    def failed(self) -> bool:
        return bool(self._last_error)

    @property
    def last_error(self) -> str:
        return self._last_error

    def publish_progress(self, preparations: list[dict[str, Any]]) -> None:
        if self._closing or self._closed or self.failed:
            return
        self._pending_progress = preparations
        self._event.set()

    def publish_stream(self, markdown: str) -> None:
        if self._closing or self._closed or self.failed:
            return
        self._pending_markdown = markdown
        self._event.set()

    async def _run(self) -> None:
        try:
            while True:
                await self._event.wait()
                self._event.clear()
                progress = self._pending_progress
                markdown = self._pending_markdown
                self._pending_progress = None
                self._pending_markdown = None
                if progress is not None:
                    await self._session.update_progress(progress)
                if markdown is not None:
                    await self._session.stream(markdown)
                if (
                    self._closing
                    and self._pending_progress is None
                    and self._pending_markdown is None
                ):
                    return
                if self._pending_progress is not None or self._pending_markdown is not None:
                    self._event.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            self._pending_progress = None
            self._pending_markdown = None
            logger.warning(f"钉钉 AI Card 增量更新已停止: {exc}")

    async def _stop_worker(self, *, final_progress: list[dict[str, Any]] | None) -> None:
        if self._closed:
            return
        self._pending_progress = final_progress
        self._pending_markdown = None
        self._closing = True
        self._event.set()
        try:
            await asyncio.wait_for(
                self._worker,
                timeout=_CARD_BUFFER_CLOSE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            self._worker.cancel()
            raise TimeoutError("钉钉 AI Card 增量更新队列关闭超时") from exc
        finally:
            self._closed = True
        if self._last_error:
            raise RuntimeError(self._last_error)

    async def finish(
        self,
        markdown: str,
        *,
        buttons: list[dict[str, Any]],
    ) -> None:
        await self._stop_worker(final_progress=[])
        await self._session.finish(markdown, buttons=buttons)

    async def fail(self, message: str) -> None:
        try:
            await self._stop_worker(final_progress=None)
        except Exception:
            pass
        await self._session.fail(message)


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
                    "preparations": [
                        {"name": "正在处理 · 理解需求", "progress": 0}
                    ],
                    "charts": [],
                    "config": {"autoLayout": True},
                }
            )
            sdk_errors.reset()
            card_instance_id = await _await_card_request(
                "创建钉钉 AI Card",
                replier.async_create_and_deliver_card(
                    normalized_template_id,
                    card_data,
                ),
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
        card = await _await_card_request(
            "创建内置钉钉 AI Card",
            asyncio.to_thread(
                handler.ai_markdown_card_start,
                incoming,
                "AI 中枢",
            ),
        )
        if getattr(card, "card_instance_id", None):
            return _LegacyMarkdownCardSession(card)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"创建内置钉钉 AI Card 失败: {exc}")
    return None
