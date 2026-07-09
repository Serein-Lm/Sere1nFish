"""
系统5 — 自动聊天(串起系统 1-4),真正做到「加人后自动聊」。

每轮:读屏(视觉) → 结构化状态(是谁/最后一条谁发的/有无未回复) →
- 不在该联系人对话界面:可选自动导航过去,本轮不回;
- 对方有未回复消息:沉淀画像 → 生成回复 →(auto_send)自动发送,否则只产建议供前端随时查看;
- 其它情况:只观察。
去重避免对同一条消息重复回复;会话快照落库;每个动作经事件总线推送给前端。

watcher:周期性检测新好友请求 → 自动通过 → 进入对话 → 为新联系人起一条自动聊天。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.mobile.chat_assist import (
    read_screen,
    parse_chat_state,
    derive_contact_id,
    compose_one_reply,
    send_reply,
)
from core.mobile.profiling import analyze_and_update, format_profile_for_prompt
from core.mobile.events import publish


def _publish_ac(state: "AutoChatState", event: str, **extra: Any) -> None:
    """统一推送自动聊天事件。"""
    publish(
        {
            "type": "auto_chat",
            "device_id": state.device_id,
            "contact_id": state.contact_id,
            "project_id": state.project_id,
            "data": {
                "task_id": state.task_id,
                "project_id": state.project_id,
                "event": event,
                "rounds": state.rounds,
                "replies_sent": state.replies_sent,
                **extra,
            },
        }
    )


@dataclass
class AutoChatState:
    task_id: str
    device_id: str
    contact_id: str | None = None  # 可空:未知则从屏幕识别推导
    project_id: str | None = None
    contact_name: str | None = None
    my_background: str = ""
    goal: str = ""
    platform: str | None = None
    owner: str | None = None
    interval: float = 8.0
    auto_send: bool = False
    ensure_chat: bool = False  # 不在对话界面时是否自动导航过去
    send_button: dict[str, int] | None = None
    running: bool = True
    rounds: int = 0
    replies_sent: int = 0
    observed: int = 0
    skipped: int = 0
    last_reply: str = ""
    last_suggestion: str = ""
    last_replied_message: str = ""  # 去重:已回复过的最后一条消息
    last_state: dict[str, Any] | None = None
    last_error: str | None = None
    started_at: float = field(default_factory=time.time)


class AutoChatManager:
    """自动聊天会话管理(单例)。"""

    _instance: "AutoChatManager | None" = None

    def __init__(self) -> None:
        self._sessions: dict[str, AutoChatState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._watchers: dict[str, asyncio.Task[None]] = {}
        self._watch_owners: dict[str, str | None] = {}

    @classmethod
    def get_instance(cls) -> "AutoChatManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(
        self,
        device_id: str,
        contact_id: str | None = None,
        *,
        project_id: str | None = None,
        contact_name: str | None = None,
        my_background: str = "",
        goal: str = "",
        platform: str | None = None,
        owner: str | None = None,
        interval: float = 8.0,
        auto_send: bool = False,
        ensure_chat: bool = False,
        send_button: dict[str, int] | None = None,
    ) -> str:
        task_id = uuid.uuid4().hex[:12]
        state = AutoChatState(
            task_id=task_id,
            device_id=device_id,
            contact_id=contact_id,
            project_id=project_id,
            contact_name=contact_name,
            my_background=my_background,
            goal=goal,
            platform=platform,
            owner=owner,
            interval=max(2.0, float(interval)),
            auto_send=auto_send,
            ensure_chat=ensure_chat,
            send_button=send_button,
        )
        self._sessions[task_id] = state
        self._tasks[task_id] = asyncio.create_task(self._loop(state))
        return task_id

    def stop(
        self,
        task_id: str,
        *,
        owner: str | None = None,
        is_admin: bool = False,
    ) -> bool:
        state = self._sessions.get(task_id)
        ok = False
        if state is not None:
            if state.owner and owner != state.owner and not is_admin:
                return False
            state.running = False
            ok = True
        task = self._tasks.get(task_id)
        if task:
            task.cancel()
        watcher = self._watchers.get(task_id)
        if watcher:
            watcher.cancel()
            ok = True
        return ok

    def status(self, task_id: str | None = None) -> Any:
        if task_id:
            state = self._sessions.get(task_id)
            return self._dump(state) if state else None
        return [self._dump(s) for s in self._sessions.values()]

    @staticmethod
    def _dump(s: AutoChatState) -> dict[str, Any]:
        return {
            "task_id": s.task_id,
            "device_id": s.device_id,
            "contact_id": s.contact_id,
            "project_id": s.project_id,
            "contact_name": s.contact_name,
            "goal": s.goal,
            "owner": s.owner,
            "running": s.running,
            "auto_send": s.auto_send,
            "ensure_chat": s.ensure_chat,
            "rounds": s.rounds,
            "replies_sent": s.replies_sent,
            "observed": s.observed,
            "skipped": s.skipped,
            "last_reply": s.last_reply,
            "last_suggestion": s.last_suggestion,
            "last_state": s.last_state,
            "last_error": s.last_error,
            "started_at": s.started_at,
        }

    async def _persist(self, state: AutoChatState) -> None:
        try:
            from api.db.mongodb import get_db
            from api.dao import auto_chat_sessions as acs_dao

            await acs_dao.upsert_session(get_db(), self._dump(state))
        except Exception:  # noqa: BLE001
            pass

    async def _navigate_to_chat(self, state: AutoChatState) -> None:
        """用执行层导航到与该联系人的对话界面(best-effort)。"""
        if not state.contact_name:
            return
        from core.mobile.executor import run_task_stream

        plat = state.platform or "微信"
        task = f"打开{plat},进入与「{state.contact_name}」的聊天对话界面"
        _publish_ac(state, "navigating", contact_name=state.contact_name)
        try:
            async for _event in run_task_stream(
                state.device_id,
                task,
                max_steps=8,
                task_id=f"acnav-{state.task_id}",
                project_id=state.project_id,
                contact_id=state.contact_id,
                owner=state.owner,
            ):
                if not state.running:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            state.last_error = f"导航失败: {exc}"

    async def _save_suggestion(self, state: AutoChatState, text: str, analysis: str) -> None:
        try:
            from api.db.mongodb import get_db
            from api.dao import chat_suggestions as cs_dao

            key = state.contact_id or f"device:{state.device_id}"
            await cs_dao.save_suggestions(
                get_db(),
                key,
                {
                    "device_id": state.device_id,
                    "contact_id": state.contact_id,
                    "project_id": state.project_id,
                    "suggestions": text,
                    "screen_analysis": analysis,
                },
            )
        except Exception:  # noqa: BLE001
            pass

    async def _loop(self, state: AutoChatState) -> None:
        _publish_ac(state, "started")
        await self._persist(state)
        try:
            while state.running:
                try:
                    # 1) 读屏(视觉)
                    screen = await read_screen(
                        state.device_id,
                        project_id=state.project_id,
                        task_id=state.task_id,
                        contact_id=state.contact_id,
                        source="auto_chat",
                    )
                    analysis = screen.get("analysis", "")
                    # 2) 结构化状态
                    chstate = await parse_chat_state(analysis)
                    state.rounds += 1
                    # 3) 身份:固定 contact_id 优先,否则从屏幕推导
                    if chstate.contact_name:
                        state.contact_name = chstate.contact_name
                    if not state.contact_id:
                        derived = derive_contact_id(state.platform, chstate.contact_name)
                        if derived:
                            state.contact_id = derived
                    state.last_state = {
                        "is_chat_screen": chstate.is_chat_screen,
                        "contact_name": chstate.contact_name,
                        "last_from": chstate.last_from,
                        "unreplied": chstate.unreplied,
                    }

                    # 4) 不在对话界面 → 可选导航,本轮不回
                    if not chstate.is_chat_screen:
                        state.skipped += 1
                        _publish_ac(state, "not_in_chat")
                        if state.ensure_chat and state.contact_name:
                            await self._navigate_to_chat(state)
                        await self._persist(state)
                        await asyncio.sleep(state.interval)
                        continue

                    # 5) 仅当「对方发了且未回复」才处理
                    other_unreplied = (
                        chstate.last_from == "other" and chstate.unreplied
                    )
                    if not other_unreplied:
                        state.observed += 1
                        _publish_ac(state, "observed")
                        await self._persist(state)
                        await asyncio.sleep(state.interval)
                        continue

                    # 去重:同一条消息已处理过则跳过
                    if (
                        chstate.last_message
                        and chstate.last_message == state.last_replied_message
                    ):
                        _publish_ac(state, "already_handled")
                        await asyncio.sleep(state.interval)
                        continue

                    # 6) 沉淀画像(对方有新消息时才更新)
                    profile = None
                    if state.contact_id:
                        profile = await analyze_and_update(
                            state.device_id,
                            state.contact_id,
                            name=state.contact_name,
                            platform=state.platform,
                            screen_analysis=analysis,
                            project_id=state.project_id,
                            task_id=state.task_id,
                            source="auto_chat",
                            evidence={
                                key: screen.get(key)
                                for key in (
                                    "screenshot_id",
                                    "screenshot_url",
                                    "width",
                                    "height",
                                )
                                if screen.get(key) is not None
                            },
                        )

                    # 7) 生成回复
                    reply = await compose_one_reply(
                        state.device_id,
                        my_background=state.my_background,
                        contact_profile=format_profile_for_prompt(profile),
                        screen_analysis=analysis,
                        project_id=state.project_id,
                        task_id=state.task_id,
                        contact_id=state.contact_id,
                        goal=state.goal,
                    )
                    if reply:
                        if state.auto_send:
                            await send_reply(
                                state.device_id,
                                reply,
                                send_button=state.send_button,
                                project_id=state.project_id,
                                task_id=state.task_id,
                                contact_id=state.contact_id,
                            )
                            state.last_reply = reply
                            state.replies_sent += 1
                            _publish_ac(state, "reply_sent", reply=reply)
                        else:
                            # 观察模式:不发,只把建议落库+推送供前端随时查看
                            state.last_suggestion = reply
                            await self._save_suggestion(state, reply, analysis)
                            publish(
                                {
                                    "type": "suggestion",
                                    "device_id": state.device_id,
                                    "contact_id": state.contact_id,
                                    "project_id": state.project_id,
                                    "data": {"suggestions": reply},
                                }
                            )
                            _publish_ac(state, "suggestion", suggestion=reply)
                        state.last_replied_message = chstate.last_message or ""

                    state.last_error = None
                    await self._persist(state)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    state.last_error = str(exc)
                    _publish_ac(state, "error", message=str(exc))

                await asyncio.sleep(state.interval)
        except asyncio.CancelledError:
            pass
        finally:
            state.running = False
            _publish_ac(state, "stopped")
            await self._persist(state)

    # ============ 新好友 watcher(加人后自动聊) ============

    async def start_watch(
        self,
        device_id: str,
        *,
        project_id: str | None = None,
        platform: str = "微信",
        my_background: str = "",
        auto_accept: bool = True,
        auto_send: bool = False,
        interval: float = 20.0,
        send_button: dict[str, int] | None = None,
        owner: str | None = None,
    ) -> str:
        watch_id = "watch-" + uuid.uuid4().hex[:8]
        self._watch_owners[watch_id] = owner
        self._watchers[watch_id] = asyncio.create_task(
            self._watch_loop(
                watch_id,
                device_id,
                project_id=project_id,
                platform=platform,
                my_background=my_background,
                auto_accept=auto_accept,
                auto_send=auto_send,
                interval=max(8.0, float(interval)),
                send_button=send_button,
                owner=owner,
            )
        )
        return watch_id

    def stop_watch(
        self,
        watch_id: str,
        *,
        owner: str | None = None,
        is_admin: bool = False,
    ) -> bool:
        watch_owner = self._watch_owners.get(watch_id)
        if watch_owner and owner != watch_owner and not is_admin:
            return False
        task = self._watchers.get(watch_id)
        if not task:
            return False
        task.cancel()
        return True

    async def _watch_loop(
        self,
        watch_id: str,
        device_id: str,
        *,
        project_id: str | None,
        platform: str,
        my_background: str,
        auto_accept: bool,
        auto_send: bool,
        interval: float,
        send_button: dict[str, int] | None,
        owner: str | None,
    ) -> None:
        from core.mobile.executor import run_task_stream

        started_contacts: set[str] = set()
        publish(
            {
                "type": "auto_chat_watch",
                "device_id": device_id,
                "project_id": project_id,
                "data": {"watch_id": watch_id, "event": "started"},
            }
        )
        accept_clause = (
            "若有新好友请求则点击通过/接受,然后进入与该新好友的聊天对话界面;"
            if auto_accept
            else "若有新好友请求请仅进入其资料或聊天界面(不要点通过);"
        )
        nav_task = (
            f"打开{platform}查看是否有新的好友/朋友请求。{accept_clause}若没有新请求则停在通讯录。"
        )
        try:
            while True:
                try:
                    # 1) 用执行层去检测并(可选)通过新好友、进入对话
                    async for _e in run_task_stream(
                        device_id,
                        nav_task,
                        max_steps=12,
                        task_id=f"{watch_id}-scan",
                        project_id=project_id,
                        owner=owner,
                    ):
                        pass
                    # 2) 读当前屏,若已进入某新联系人的对话 → 为其起自动聊天
                    screen = await read_screen(
                        device_id,
                        project_id=project_id,
                        task_id=watch_id,
                        source="auto_chat_watch",
                    )
                    chstate = await parse_chat_state(screen.get("analysis", ""))
                    if (
                        chstate.is_chat_screen
                        and chstate.contact_name
                        and chstate.contact_name not in started_contacts
                    ):
                        started_contacts.add(chstate.contact_name)
                        cid = derive_contact_id(platform, chstate.contact_name)
                        task_id = await self.start(
                            device_id,
                            cid,
                            project_id=project_id,
                            contact_name=chstate.contact_name,
                            platform=platform,
                            my_background=my_background,
                            auto_send=auto_send,
                            ensure_chat=True,
                            send_button=send_button,
                            owner=owner,
                        )
                        publish(
                            {
                                "type": "auto_chat_watch",
                                "device_id": device_id,
                                "contact_id": cid,
                                "project_id": project_id,
                                "data": {
                                    "watch_id": watch_id,
                                    "event": "new_contact",
                                    "contact_name": chstate.contact_name,
                                    "auto_chat_task_id": task_id,
                                },
                            }
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    publish(
                        {
                            "type": "auto_chat_watch",
                            "device_id": device_id,
                            "project_id": project_id,
                            "data": {
                                "watch_id": watch_id,
                                "event": "error",
                                "message": str(exc),
                            },
                        }
                    )
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        finally:
            self._watchers.pop(watch_id, None)
            self._watch_owners.pop(watch_id, None)
            publish(
                {
                    "type": "auto_chat_watch",
                    "device_id": device_id,
                    "project_id": project_id,
                    "data": {"watch_id": watch_id, "event": "stopped"},
                }
            )
