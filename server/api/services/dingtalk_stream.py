"""DingTalk Stream Mode adapter and lifecycle manager.

Inbound transport, AI Card updates and SDK details stay in this adapter. The
business bridge only sees the unified AI Hub event stream.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

from api.services.dingtalk_ai_card import (
    BufferedDingTalkCardSession,
    create_ai_card_session,
)
from api.services.dingtalk_card import DingTalkCardRenderer, build_artifact_buttons
from core.background import spawn_background
from core.logger import get_logger

logger = get_logger("api.services.dingtalk_stream")

_MAX_CARD_CHARS = 12_000
_STREAM_INTERVAL_SECONDS = 0.8
_STREAM_MIN_DELTA = 48
_CARD_BOOTSTRAP_FINAL_GRACE_SECONDS = 0.5
_MESSAGE_DEDUPE_TTL_SECONDS = 10 * 60
_MESSAGE_DEDUPE_MAX_ITEMS = 2_048


@dataclass
class _ConversationTurnGate:
    lock: asyncio.Lock
    users: int = 0


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
        self._seen_message_ids: dict[str, float] = {}
        self._conversation_gates: dict[str, _ConversationTurnGate] = {}
        self._status: dict[str, Any] = {
            "state": "stopped",
            "connected": False,
            "last_error": "",
            "last_connected_at": None,
            "last_message_at": None,
        }

    def status(self) -> dict[str, Any]:
        return {"bot_name": self.bot_name, **self._status}

    def _claim_message_id(self, message_id: Any) -> bool:
        """Accept one Stream delivery and reject SDK redelivery duplicates."""
        normalized = str(message_id or "").strip()
        if not normalized:
            return True
        now = time.monotonic()
        cutoff = now - _MESSAGE_DEDUPE_TTL_SECONDS
        previous = self._seen_message_ids.get(normalized)
        if previous is not None and previous >= cutoff:
            return False
        if len(self._seen_message_ids) >= _MESSAGE_DEDUPE_MAX_ITEMS:
            for key, seen_at in list(self._seen_message_ids.items()):
                if seen_at < cutoff:
                    self._seen_message_ids.pop(key, None)
            while len(self._seen_message_ids) >= _MESSAGE_DEDUPE_MAX_ITEMS:
                self._seen_message_ids.pop(next(iter(self._seen_message_ids)))
        self._seen_message_ids[normalized] = now
        return True

    @asynccontextmanager
    async def _conversation_turn(self, key: str):
        """Serialize turns that share one persisted conversation context."""
        gate = self._conversation_gates.get(key)
        if gate is None:
            gate = _ConversationTurnGate(lock=asyncio.Lock())
            self._conversation_gates[key] = gate
        gate.users += 1
        try:
            async with gate.lock:
                yield
        finally:
            gate.users -= 1
            if gate.users <= 0 and self._conversation_gates.get(key) is gate:
                self._conversation_gates.pop(key, None)

    @staticmethod
    def _conversation_turn_key(incoming: Any) -> str:
        conversation_id = str(
            getattr(incoming, "conversation_id", "")
            or getattr(incoming, "sender_id", "")
            or "unknown"
        )
        conversation_type = str(
            getattr(incoming, "conversation_type", "") or ""
        ).strip().casefold()
        if conversation_type in {"2", "group", "group_chat", "groupchat"}:
            sender_id = str(
                getattr(incoming, "sender_staff_id", "")
                or getattr(incoming, "sender_id", "")
                or "unknown"
            )
            return f"{conversation_id}:member:{sender_id}"
        return conversation_id

    async def _process_serialized_message(
        self,
        handler: Any,
        incoming: Any,
        query: str,
    ) -> None:
        async with self._conversation_turn(self._conversation_turn_key(incoming)):
            await self._process_message(handler, incoming, query)

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
                if not adapter._claim_message_id(getattr(incoming, "message_id", "")):
                    return dingtalk_stream.AckMessage.STATUS_OK, "ignored duplicate message"
                spawn_background(
                    adapter._process_serialized_message(self, incoming, query),
                    name=f"dingtalk_hub:{adapter.bot_name}",
                )
                return dingtalk_stream.AckMessage.STATUS_OK, "accepted"

        return HubChatbotHandler()

    async def _process_message(self, handler: Any, incoming: Any, query: str) -> None:
        from api.services.dingtalk_bridge import (
            build_dingtalk_conversation_id,
            clear_hub_context,
            format_context_cleared_message,
            is_clear_context_command,
            run_hub_query,
        )
        from crawler_tools.dingtalk_bot import reply_to_session_webhook

        sender_id = str(
            getattr(incoming, "sender_staff_id", "")
            or getattr(incoming, "sender_id", "")
            or "unknown"
        )
        source_conversation_id = str(
            getattr(incoming, "conversation_id", "") or sender_id
        )
        hub_conversation_id = build_dingtalk_conversation_id(
            bot_name=self.bot_name,
            conversation_id=source_conversation_id,
            sender_id=sender_id,
            conversation_type=str(getattr(incoming, "conversation_type", "") or ""),
        )
        started_at = time.monotonic()
        logger.info(
            "钉钉 Stream 消息开始 bot=%s conversation=%s sender=%s chars=%s",
            self.bot_name,
            hub_conversation_id,
            sender_id,
            len(query),
        )

        async def _send_markdown(title: str, text: str) -> None:
            result = await reply_to_session_webhook(
                str(getattr(incoming, "session_webhook", "") or ""),
                title=title,
                text=text,
                at_user_ids=[sender_id] if sender_id != "unknown" else [],
            )
            if not result.success:
                logger.warning(f"钉钉 Stream 回退回复失败: {result.message}")

        if is_clear_context_command(query):
            try:
                result = await clear_hub_context(hub_conversation_id)
                await _send_markdown("AI 中枢", format_context_cleared_message(result))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"清空钉钉上下文失败: {exc}")
                with contextlib.suppress(Exception):
                    await _send_markdown("AI 中枢", "清空上下文失败，请稍后重试。")
            return

        renderer = DingTalkCardRenderer()
        card: BufferedDingTalkCardSession | None = None
        card_bootstrap_task: asyncio.Task[Any] | None = None
        card_streaming = bool(self.config.get("ai_card_streaming", True))
        if card_streaming:
            # Card transport starts alongside the Agent. A slow DingTalk create
            # request must not delay context loading, planning or model output.
            card_bootstrap_task = asyncio.create_task(
                create_ai_card_session(
                    handler,
                    incoming,
                    query=query,
                    template_id=str(self.config.get("ai_card_template_id") or ""),
                ),
                name=f"dingtalk_card_bootstrap:{self.bot_name}",
            )

        last_sent_content = ""
        last_sent_at = 0.0
        last_preparations = ""

        async def _activate_card(
            *,
            wait_seconds: float = 0.0,
        ) -> BufferedDingTalkCardSession | None:
            nonlocal card, card_bootstrap_task
            if card is not None or card_bootstrap_task is None:
                return card
            task = card_bootstrap_task
            if not task.done() and wait_seconds <= 0:
                return None
            try:
                if task.done():
                    card_session = task.result()
                else:
                    card_session = await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=wait_seconds,
                    )
            except asyncio.TimeoutError:
                return None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"初始化钉钉 AI Card 失败，回退 Markdown: {exc}")
                card_bootstrap_task = None
                return None
            card_bootstrap_task = None
            if card_session is not None:
                card = BufferedDingTalkCardSession(card_session)
            return card

        async def _cancel_card_bootstrap() -> None:
            nonlocal card_bootstrap_task
            task = card_bootstrap_task
            card_bootstrap_task = None
            if task is None:
                return
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        async def _on_event(event: dict[str, Any]) -> None:
            nonlocal card, last_sent_content, last_sent_at, last_preparations
            renderer.consume(event)
            await _activate_card()
            if card is None:
                return
            if card.failed:
                logger.warning(
                    "钉钉 AI Card 增量更新失败，后续回退 Markdown: %s",
                    card.last_error,
                )
                failed_card = card
                card = None
                spawn_background(
                    failed_card.fail("卡片增量更新失败，最终结果将以普通消息发送。"),
                    name=f"dingtalk_card_fail:{self.bot_name}",
                )
                return

            event_type = str(event.get("event") or "")
            if event_type in {"start", "end", "error"}:
                preparations = renderer.render_preparations()
                serialized = json.dumps(preparations, ensure_ascii=False, sort_keys=True)
                if serialized != last_preparations:
                    card.publish_progress(preparations)
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

            card.publish_stream(preview)
            last_sent_content = preview
            last_sent_at = now

        try:
            final_text, artifacts = await run_hub_query(
                query,
                owner=f"dingtalk:{sender_id}",
                conversation_id=hub_conversation_id,
                channel="dingtalk_stream",
                on_event=_on_event,
            )
            from api.db.mongodb import get_db
            from api.services.artifact_access import attach_temporary_download_urls

            try:
                artifacts = await attach_temporary_download_urls(
                    get_db(),
                    artifacts,
                    owner=f"dingtalk:{sender_id}",
                    expires_seconds=3600,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("钉钉产物临时链接生成失败，回退登录下载: %s", exc)
            final_markdown = renderer.render_final(
                final_text,
                artifacts,
                base_url=str(self.config.get("public_base_url") or ""),
                max_chars=_MAX_CARD_CHARS,
                include_execution_summary=(
                    card is None or not card.has_progress_panel
                ),
            )
            if card is None:
                await _activate_card(
                    wait_seconds=_CARD_BOOTSTRAP_FINAL_GRACE_SECONDS,
                )
            if card is not None:
                buttons = self._artifact_buttons(artifacts)
                try:
                    await card.finish(final_markdown, buttons=buttons)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"结束钉钉 AI Card 失败，回退 Markdown: {exc}")
                    spawn_background(
                        card.fail("卡片更新失败，最终结果已通过普通消息发送。"),
                        name=f"dingtalk_card_final_fail:{self.bot_name}",
                    )
                    await _send_markdown("AI 中枢回复", final_markdown)
            else:
                await _send_markdown("AI 中枢回复", final_markdown)
            logger.info(
                "钉钉 Stream 消息完成 bot=%s conversation=%s elapsed=%.2fs artifacts=%s",
                self.bot_name,
                hub_conversation_id,
                time.monotonic() - started_at,
                len(artifacts),
            )
        except asyncio.CancelledError:
            logger.warning(
                "钉钉 Stream 消息因服务重载中断 bot=%s conversation=%s elapsed=%.2fs",
                self.bot_name,
                hub_conversation_id,
                time.monotonic() - started_at,
            )
            interrupted = "服务正在重载，本次处理已中断，请稍后重新发送该问题。"
            if card is None:
                with contextlib.suppress(Exception):
                    await _activate_card(wait_seconds=0.25)
            if card is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        asyncio.shield(card.fail(interrupted)),
                        timeout=12,
                    )
            else:
                with contextlib.suppress(Exception):
                    await _send_markdown("AI 中枢", interrupted)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"钉钉 Stream AI 中枢处理失败: {exc}")
            error_text = f"处理问题时发生错误：{exc}"[:1000]
            if card is None:
                with contextlib.suppress(Exception):
                    await _activate_card(wait_seconds=0.25)
            if card is not None:
                try:
                    await card.fail(error_text)
                except Exception:
                    with contextlib.suppress(Exception):
                        await _send_markdown("AI 中枢", error_text)
            else:
                with contextlib.suppress(Exception):
                    await _send_markdown("AI 中枢", error_text)
        finally:
            await _cancel_card_bootstrap()

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
