"""DingTalk Stream Mode adapter and lifecycle manager.

Inbound transport, AI Card updates and SDK details stay in this adapter. The
business bridge only sees the unified AI Hub event stream.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

from api.services.dingtalk_ai_card import create_ai_card_session
from api.services.dingtalk_card import DingTalkCardRenderer, build_artifact_buttons
from core.background import spawn_background
from core.logger import get_logger

logger = get_logger("api.services.dingtalk_stream")

_MAX_CARD_CHARS = 12_000
_STREAM_INTERVAL_SECONDS = 0.8
_STREAM_MIN_DELTA = 48


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _message_text(incoming: Any) -> str:
    values = incoming.get_text_list() or []
    return "\n".join(str(value).strip() for value in values if str(value).strip()).strip()


class DingTalkStreamAdapter:
    def __init__(self, bot_name: str, config: dict[str, Any]) -> None:
        self.bot_name = bot_name
        self.config = dict(config)
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None
        self._client: Any = None
        self._websocket: Any = None
        self._status: dict[str, Any] = {
            "state": "stopped",
            "connected": False,
            "last_error": "",
            "last_connected_at": None,
            "last_message_at": None,
        }

    def status(self) -> dict[str, Any]:
        return {"bot_name": self.bot_name, **self._status}

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._status.update(state="connecting", connected=False, last_error="")
        self._task = spawn_background(self._run(), name=f"dingtalk_stream:{self.bot_name}")

    async def stop(self) -> None:
        self._stop_event.set()
        websocket = self._websocket
        if websocket is not None:
            with contextlib.suppress(Exception):
                await websocket.close()
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._task = None
        self._status.update(state="stopped", connected=False)

    async def _run(self) -> None:
        try:
            import dingtalk_stream
            import websockets
        except ImportError as exc:
            self._status.update(state="unavailable", connected=False, last_error=str(exc))
            logger.warning(f"钉钉 Stream SDK 未安装: {exc}")
            return

        client_id = str(self.config.get("client_id") or "").strip()
        client_secret = str(self.config.get("client_secret") or "").strip()
        credential = dingtalk_stream.Credential(client_id, client_secret)
        client = dingtalk_stream.DingTalkStreamClient(credential)
        handler = self._create_handler(dingtalk_stream)
        client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, handler)
        client.pre_start()
        self._client = client

        retry_seconds = max(2, min(int(self.config.get("reconnect_seconds") or 5), 60))
        while not self._stop_event.is_set():
            try:
                self._status.update(state="connecting", connected=False)
                connection = await asyncio.wait_for(
                    asyncio.to_thread(client.open_connection), timeout=25
                )
                if not connection:
                    raise RuntimeError("钉钉未返回 Stream 连接信息，请检查 Client ID/Secret 和机器人发布状态")
                uri = f"{connection['endpoint']}?ticket={quote_plus(connection['ticket'])}"
                async with websockets.connect(
                    uri,
                    open_timeout=20,
                    ping_interval=30,
                    ping_timeout=20,
                    close_timeout=5,
                ) as websocket:
                    self._websocket = websocket
                    client.websocket = websocket
                    self._status.update(
                        state="connected",
                        connected=True,
                        last_error="",
                        last_connected_at=_now(),
                    )
                    logger.info(f"钉钉 Stream 已连接 bot={self.bot_name}")
                    async for raw_message in websocket:
                        if self._stop_event.is_set():
                            break
                        self._status["last_message_at"] = _now()
                        try:
                            payload = json.loads(raw_message)
                        except (TypeError, ValueError):
                            logger.warning(f"钉钉 Stream 收到非法消息 bot={self.bot_name}")
                            continue
                        spawn_background(
                            client.background_task(payload),
                            name=f"dingtalk_message:{self.bot_name}",
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._status.update(
                    state="reconnecting",
                    connected=False,
                    last_error=str(exc),
                )
                logger.warning(f"钉钉 Stream 连接异常 bot={self.bot_name}: {exc}")
            finally:
                self._websocket = None
                client.websocket = None

            if not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=retry_seconds)
                except asyncio.TimeoutError:
                    pass

    def _create_handler(self, dingtalk_stream: Any) -> Any:
        adapter = self

        class HubChatbotHandler(dingtalk_stream.ChatbotHandler):
            async def process(self, callback: Any):
                incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
                query = _message_text(incoming)
                if not query:
                    return dingtalk_stream.AckMessage.STATUS_OK, "ignored empty message"
                spawn_background(
                    adapter._process_message(self, incoming, query),
                    name=f"dingtalk_hub:{adapter.bot_name}",
                )
                return dingtalk_stream.AckMessage.STATUS_OK, "accepted"

        return HubChatbotHandler()

    async def _process_message(self, handler: Any, incoming: Any, query: str) -> None:
        from api.services.dingtalk_bridge import run_hub_query
        from crawler_tools.dingtalk_bot import reply_to_session_webhook

        renderer = DingTalkCardRenderer()
        card = None
        card_streaming = bool(self.config.get("ai_card_streaming", True))
        if card_streaming:
            card = await create_ai_card_session(
                handler,
                incoming,
                query=query,
                template_id=str(self.config.get("ai_card_template_id") or ""),
            )

        last_sent_content = ""
        last_sent_at = 0.0
        last_preparations = ""

        async def _on_event(event: dict[str, Any]) -> None:
            nonlocal card, last_sent_content, last_sent_at, last_preparations
            renderer.consume(event)
            if card is None:
                return

            event_type = str(event.get("event") or "")
            if event_type in {"start", "end", "error"}:
                preparations = renderer.render_preparations()
                serialized = json.dumps(preparations, ensure_ascii=False, sort_keys=True)
                if serialized != last_preparations:
                    try:
                        await card.update_progress(preparations)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"更新钉钉 AI Card 进度失败，后续回退 Markdown: {exc}")
                        card = None
                        return
                    last_preparations = serialized

            # The primary content variable only receives the synthesized answer.
            # Specialist reasoning remains available in the folded progress area.
            if event_type != "content" or not renderer.answer_started:
                return

            preview = renderer.render_streaming(max_chars=_MAX_CARD_CHARS)
            if not preview or preview == last_sent_content:
                return

            now = time.monotonic()
            visible_delta = abs(len(preview) - len(last_sent_content))
            if (
                visible_delta < _STREAM_MIN_DELTA
                and now - last_sent_at < _STREAM_INTERVAL_SECONDS
            ):
                return

            try:
                await card.stream(preview)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"更新钉钉 AI Card 失败，后续回退 Markdown: {exc}")
                card = None
                return
            last_sent_content = preview
            last_sent_at = now

        sender_id = str(
            getattr(incoming, "sender_staff_id", "")
            or getattr(incoming, "sender_id", "")
            or "unknown"
        )
        conversation_id = str(getattr(incoming, "conversation_id", "") or sender_id)

        async def _send_markdown(title: str, text: str) -> None:
            result = await reply_to_session_webhook(
                str(getattr(incoming, "session_webhook", "") or ""),
                title=title,
                text=text,
                at_user_ids=[sender_id] if sender_id != "unknown" else [],
            )
            if not result.success:
                logger.warning(f"钉钉 Stream 回退回复失败: {result.message}")

        try:
            final_text, artifacts = await run_hub_query(
                query,
                owner=f"dingtalk:{sender_id}",
                conversation_id=f"dingtalk:{self.bot_name}:{conversation_id}",
                channel="dingtalk_stream",
                on_event=_on_event,
            )
            final_markdown = renderer.render_final(
                final_text,
                artifacts,
                base_url=str(self.config.get("public_base_url") or ""),
                max_chars=_MAX_CARD_CHARS,
                include_execution_summary=(
                    card is None or not card.has_progress_panel
                ),
            )
            if card is not None:
                buttons = self._artifact_buttons(artifacts)
                try:
                    await card.update_progress(renderer.render_preparations(final=True))
                    await card.finish(final_markdown, buttons=buttons)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"结束钉钉 AI Card 失败，回退 Markdown: {exc}")
                    await _send_markdown("AI 中枢回复", final_markdown)
            else:
                await _send_markdown("AI 中枢回复", final_markdown)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"钉钉 Stream AI 中枢处理失败: {exc}")
            error_text = f"处理问题时发生错误：{exc}"[:1000]
            if card is not None:
                try:
                    await card.fail(error_text)
                except Exception:
                    with contextlib.suppress(Exception):
                        await _send_markdown("AI 中枢", error_text)
            else:
                with contextlib.suppress(Exception):
                    await _send_markdown("AI 中枢", error_text)

    def _artifact_buttons(self, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return build_artifact_buttons(
            artifacts,
            base_url=str(self.config.get("public_base_url") or ""),
        )


class DingTalkStreamManager:
    _instance: "DingTalkStreamManager | None" = None

    def __init__(self) -> None:
        self._adapters: dict[str, DingTalkStreamAdapter] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "DingTalkStreamManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _enabled(config: dict[str, Any]) -> bool:
        return bool(
            config.get("enabled", True)
            and config.get("stream_enabled", False)
            and str(config.get("client_id") or "").strip()
            and str(config.get("client_secret") or "").strip()
        )

    async def reload_all(self) -> None:
        from api.dao import config as config_dao
        from api.db.mongodb import get_db

        configs = await config_dao.list_dingtalk_configs(get_db())
        async with self._lock:
            for adapter in list(self._adapters.values()):
                await adapter.stop()
            self._adapters.clear()
            for bot_name, config in configs.items():
                if self._enabled(config):
                    adapter = DingTalkStreamAdapter(bot_name, config)
                    self._adapters[bot_name] = adapter
                    await adapter.start()

    async def reload_bot(self, bot_name: str) -> None:
        from api.dao import config as config_dao
        from api.db.mongodb import get_db

        config = await config_dao.get_dingtalk_config(get_db(), bot_name)
        async with self._lock:
            previous = self._adapters.pop(bot_name, None)
            if previous:
                await previous.stop()
            if self._enabled(config):
                adapter = DingTalkStreamAdapter(bot_name, config)
                self._adapters[bot_name] = adapter
                await adapter.start()

    async def stop(self) -> None:
        async with self._lock:
            for adapter in list(self._adapters.values()):
                await adapter.stop()
            self._adapters.clear()

    def get_status(self, bot_name: str) -> dict[str, Any]:
        adapter = self._adapters.get(bot_name)
        if adapter:
            return adapter.status()
        return {
            "bot_name": bot_name,
            "state": "stopped",
            "connected": False,
            "last_error": "",
            "last_connected_at": None,
            "last_message_at": None,
        }
