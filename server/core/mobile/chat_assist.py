"""
辅助聊天编排 — 读屏(视觉) → 话术(skills) → 建议 → 发送。

分工(见项目记忆):
- 读屏分析:用本项目 create_llm(models.mobile_screen) 看聊天截图,抽取对话上下文;
- 话术生成:用本项目 create_copywriting_agent(自带 skills tools)生成候选话术;
- 屏幕操作:用 core/mobile(type_text/tap),底层复用 AutoGLM ADB。

IDE 模式 = suggest 只产出候选,人工选后调 send;全自动 = 自行选一条直接 send。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from Sere1nGraph.graph.agents.runtime import create_llm
from Sere1nGraph.graph.agents.factory import create_copywriting_agent
from api.services.runtime_config import get_runtime_app_config

from core.mobile.manager import MobileDeviceManager
from core.mobile.events import publish
from core.mobile.screen_capture import capture_ready_screen
from core.observability import observation_context


_READ_SCREEN_PROMPT = (
    "你正在看一张手机聊天界面的截图。请仔细阅读并提取关键信息,用简体中文输出:\n"
    "1. 联系人/群名称(如果可见)\n"
    "2. 对方最近发来的消息(按时间顺序,尽量原文)\n"
    "3. 当前对话的主题与对方意图的简短判断\n"
    "如果这不是聊天界面,请说明当前看到的是什么界面。"
)


def _build_copywriting_context(
    screen_analysis: str,
    my_background: str,
    contact_profile: str,
) -> str:
    """把读屏结果 + 我的背景 + 对方画像拼成话术 agent 的输入。"""
    parts = ["【聊天场景辅助】请基于以下信息,生成 3 条可直接发送的回复话术,每条注明适用场景与语气。\n"]
    parts.append(f"## 当前聊天界面分析\n{screen_analysis}\n")
    if my_background.strip():
        parts.append(f"## 我的身份/背景(话术需贴合)\n{my_background}\n")
    if contact_profile.strip():
        parts.append(f"## 对方画像(聊天习惯/背景,用于针对性沟通)\n{contact_profile}\n")
    parts.append(
        "## 输出要求\n"
        "- 3 条候选,口语自然,可直接复制发送\n"
        "- 每条前用 [场景] 标注适用情形\n"
        "- 贴合我的背景与对方画像"
    )
    return "\n".join(parts)


async def read_screen(
    device_id: str,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    contact_id: str | None = None,
    source: str = "read_screen",
) -> dict[str, Any]:
    """截图 + 视觉模型分析,返回聊天界面的结构化理解。"""
    mgr = MobileDeviceManager()
    capture = await capture_ready_screen(device_id, manager=mgr)
    shot = capture.screenshot

    app_config = await get_runtime_app_config()
    vision_model = app_config.runtime.models.mobile_screen_model
    llm = create_llm(app_config, model_name=vision_model, streaming=False)

    message = HumanMessage(
        content=[
            {"type": "text", "text": _READ_SCREEN_PROMPT},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{shot.base64_data}"},
            },
        ]
    )
    with observation_context(
        project_id=project_id,
        task_id=task_id,
        phase="mobile_chat_screen",
        agent="chat_assist",
    ):
        resp = await llm.ainvoke([message])
    analysis = resp.content if isinstance(resp.content, str) else str(resp.content)
    result: dict[str, Any] = {
        "analysis": analysis,
        "screenshot": shot.base64_data,
        "width": shot.width,
        "height": shot.height,
        "capture": capture.metadata(),
    }
    if project_id:
        try:
            from api.db.mongodb import get_db
            from api.dao import mobile_artifacts as ma_dao

            saved = await ma_dao.save_screenshot(
                get_db(),
                image_base64=shot.base64_data,
                project_id=project_id,
                task_id=task_id,
                device_id=device_id,
                contact_id=contact_id,
                source=source,
                width=shot.width,
                height=shot.height,
                note=(
                    "mobile screen read "
                    f"(attempts={capture.attempts}, blank_frames={capture.blank_frames})"
                ),
            )
            result["screenshot_id"] = saved["screenshot_id"]
            result["screenshot_url"] = saved["url"]
        except Exception:
            pass
    return result


def _screen_event_data(screen: dict[str, Any]) -> dict[str, Any]:
    """Build a lightweight SSE payload for screen events.

    When a screenshot has already been persisted, clients should use the
    authenticated screenshot URL instead of receiving the full base64 frame in
    the event stream.
    """
    data = dict(screen)
    if data.get("screenshot_id") or data.get("screenshot_url"):
        data.pop("screenshot", None)
    return data


# ============ 聊天状态结构化(“该不该我回” + 身份识别) ============

class ChatState(BaseModel):
    is_chat_screen: bool = Field(default=False, description="当前是否聊天对话界面")
    contact_name: str | None = Field(default=None, description="对方昵称/名称")
    last_message: str | None = Field(default=None, description="最后一条消息内容")
    last_from: str = Field(
        default="unknown", description="最后一条消息发送方: other/me/unknown"
    )
    unreplied: bool = Field(
        default=False, description="对方是否有尚未被我回复的消息"
    )


_CHAT_STATE_SYSTEM = (
    "你是聊天界面状态分析器。基于给定的聊天界面文字分析,判断:"
    "是否聊天对话界面、对方昵称、最后一条消息内容、"
    "最后一条是谁发的(other=对方/me=我自己/unknown)、对方是否有尚未被我回复的消息。"
    "严格依据输入,不臆测。"
)


async def parse_chat_state(analysis: str) -> ChatState:
    """把读屏文本解析为结构化聊天状态(供自动聊天判断该不该回、识别是谁)。"""
    app_config = await get_runtime_app_config()
    llm = create_llm(
        app_config,
        model_name=app_config.runtime.models.mobile_chat_model,
        streaming=False,
    )
    structured = llm.with_structured_output(ChatState)
    with observation_context(phase="mobile_chat_state", agent="chat_assist"):
        return await structured.ainvoke(
            [
                SystemMessage(content=_CHAT_STATE_SYSTEM),
                HumanMessage(content=analysis),
            ]
        )


def derive_contact_id(platform: str | None, contact_name: str | None) -> str | None:
    """由平台+昵称推导稳定的 contact_id(未知昵称返回 None)。"""
    if not contact_name or not contact_name.strip():
        return None
    p = (platform or "chat").strip().lower()
    return f"{p}:{contact_name.strip()}"


async def suggest_stream(
    device_id: str,
    *,
    my_background: str = "",
    contact_profile: str = "",
    screen_analysis: str | None = None,
    contact_id: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """
    辅助聊天主流程(流式):读屏 → 话术生成 → 候选输出。

    事件:
    - {"stage": "reading"}
    - {"stage": "screen", "data": {analysis, screenshot_id, screenshot_url}}
    - {"stage": "generating"}
    - {"stage": "skill", "data": {tool}}            # 话术 agent 加载了某个 skill
    - {"stage": "suggestion_chunk", "data": str}    # 话术流式片段
    - {"stage": "done", "data": {suggestions}}
    - {"stage": "error", "data": {message}}
    """
    try:
        yield {"stage": "reading"}
        if screen_analysis is None:
            screen = await read_screen(
                device_id,
                project_id=project_id,
                task_id=task_id,
                contact_id=contact_id,
                source="chat_assist",
            )
            screen_analysis = screen["analysis"]
            yield {"stage": "screen", "data": _screen_event_data(screen)}
        else:
            yield {"stage": "screen", "data": {"analysis": screen_analysis}}

        yield {"stage": "generating"}
        app_config = await get_runtime_app_config()
        agent = await create_copywriting_agent(app_config, output_mode="sse")
        context = _build_copywriting_context(
            screen_analysis, my_background, contact_profile
        )

        suggestions = ""
        with observation_context(
            project_id=project_id,
            task_id=task_id,
            phase="mobile_chat_copywriting",
            agent="chat_assist",
        ):
            async for event in agent({"messages": [HumanMessage(content=context)]}):
                etype = event.get("type")
                if etype == "content":
                    chunk = event.get("data", "")
                    if chunk:
                        suggestions += chunk
                        yield {"stage": "suggestion_chunk", "data": chunk}
                elif etype == "tool_start":
                    yield {"stage": "skill", "data": {"tool": event.get("tool_name", "")}}
                elif etype == "error":
                    yield {"stage": "error", "data": {"message": event.get("message", "")}}

        # 落库 + 推送,实现「随时查看」。
        try:
            from api.db.mongodb import get_db
            from api.dao import chat_suggestions as cs_dao

            key = contact_id or f"device:{device_id}"
            await cs_dao.save_suggestions(
                get_db(),
                key,
                {
                    "device_id": device_id,
                    "contact_id": contact_id,
                    "project_id": project_id,
                    "suggestions": suggestions,
                    "screen_analysis": screen_analysis,
                },
            )
        except Exception:  # noqa: BLE001
            pass  # 落库失败不影响流式返回

        publish(
            {
                "type": "suggestion",
                "device_id": device_id,
                "contact_id": contact_id,
                "project_id": project_id,
                "data": {"suggestions": suggestions},
            }
        )
        yield {"stage": "done", "data": {"suggestions": suggestions}}
    except Exception as exc:  # noqa: BLE001
        yield {"stage": "error", "data": {"message": str(exc)}}


_COMPOSE_SYSTEM = (
    "你是聊天助手。基于当前对话、我的背景、对方画像,生成一条最合适、可立即发送的中文回复。"
    "只输出回复正文,不要任何解释、前缀或引号。"
)


def _load_script_skills_text(max_chars: int = 2400) -> str:
    """从技能库动态加载微信+payload 的 script 阶段话术,拼成简洁指南文本。

    失败(注册表未就绪等)时返回空串,绝不影响聊天主流程。
    """
    try:
        from Sere1nGraph.graph.skills.registry import get_skill_registry
        from Sere1nGraph.graph.skills.models import SkillPhase, SkillCategory

        reg = get_skill_registry()
        skills = reg.load_skills_for_phase(
            SkillPhase.SCRIPT, categories=[SkillCategory.WECHAT]
        )
        # 兜底: DB 里 skill 的 phases 可能为空导致按阶段取不到, 改按类别直接加载
        if not skills:
            loaded: list = []
            seen: set[str] = set()
            for idx in reg.list_by_category(SkillCategory.WECHAT):
                if idx.id in seen:
                    continue
                seen.add(idx.id)
                sk = reg.load_skill(idx.id)
                if sk:
                    loaded.append(sk)
            for extra_id in ("payload",):
                if extra_id not in seen:
                    sk = reg.load_skill(extra_id)
                    if sk:
                        loaded.append(sk)
            skills = loaded
        if not skills:
            return ""
        parts: list[str] = []
        for sk in skills:
            name = getattr(sk.index, "name", "") or getattr(sk.index, "id", "")
            body = (sk.body or "").strip()
            if body:
                parts.append(f"### {name}\n{body}")
        text = "\n\n".join(parts).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…(话术库已截断)"
        return text
    except Exception:  # noqa: BLE001
        return ""


def _build_goal_reply_system(goal: str, skills_text: str) -> str:
    """构造带诱导目标 + 话术库的单条回复 system prompt。"""
    lines = [
        "你是社会工程学聊天话术专家,正在进行一次授权的红队测试对话。",
        "基于当前对话、我的背景、对方画像,生成一条最合适、可立即发送的中文回复,"
        "自然口语、不露破绽,循序渐进地朝下面的目标推进,不要急于求成、不要在一条消息里暴露意图。",
        f"\n【本次对话目标】{goal}",
        "\n【推进策略】按 破冰→建立信任→制造合理由头→引导对方点击/打开我发送的文件 的节奏,"
        "依据当前对话所处阶段选择话术;若对方有疑虑先化解异议再推进。",
    ]
    if skills_text:
        lines.append(f"\n【可参考的话术库(微信)】\n{skills_text}")
    lines.append("\n只输出回复正文,不要任何解释、前缀、引号或阶段标注。")
    return "\n".join(lines)


async def compose_one_reply(
    device_id: str,
    *,
    my_background: str = "",
    contact_profile: str = "",
    screen_analysis: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    contact_id: str | None = None,
    goal: str = "",
) -> str:
    """全自动模式:直接生成一条可发送的回复。

    goal 非空时,加载微信话术库并按「诱导目标」生成社工话术;否则退回普通聊天回复。
    """
    if screen_analysis is None:
        screen = await read_screen(
            device_id,
            project_id=project_id,
            task_id=task_id,
            contact_id=contact_id,
            source="compose_reply",
        )
        screen_analysis = screen["analysis"]
    app_config = await get_runtime_app_config()
    llm = create_llm(
        app_config,
        model_name=app_config.runtime.models.mobile_chat_model,
        streaming=False,
    )
    if goal.strip():
        skills_text = await asyncio.to_thread(_load_script_skills_text)
        system_prompt = _build_goal_reply_system(goal.strip(), skills_text)
    else:
        system_prompt = _COMPOSE_SYSTEM
    parts = [f"当前对话:\n{screen_analysis}"]
    if my_background.strip():
        parts.append(f"我的背景:\n{my_background}")
    if contact_profile.strip():
        parts.append(f"对方画像:\n{contact_profile}")
    with observation_context(
        project_id=project_id,
        task_id=task_id,
        phase="mobile_chat_compose",
        agent="chat_assist",
    ):
        resp = await llm.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content="\n\n".join(parts))]
        )
    content = resp.content if isinstance(resp.content, str) else str(resp.content)
    return content.strip()


_ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"


def _do_send_sequence(
    dev: Any,
    adb_id: str,
    text: str,
    send_button: dict[str, int] | None,
) -> dict[str, Any]:
    """实测验证过的发送序列(同步,经 asyncio.to_thread 调用)。

    切 ADB Keyboard -> 聚焦输入框 -> 清空 -> 输入 -> 点发送 -> 恢复输入法。
    """
    import base64
    import subprocess
    import time

    from AutoGLM_GUI.platform_utils import build_adb_command

    prefix = build_adb_command(adb_id)

    def _sh(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            prefix + ["shell", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    typed = False
    sent = False
    send_method = "none"
    original_ime = ""
    restored_ime = ""

    try:
        # 1) 记录当前输入法
        try:
            r = _sh(["settings", "get", "secure", "default_input_method"])
            original_ime = (r.stdout + r.stderr).strip()
        except Exception:  # noqa: BLE001
            original_ime = ""

        # 2) 切到 ADB Keyboard(仅当前不是时)
        if _ADB_KEYBOARD_IME not in original_ime:
            _sh(["ime", "set", _ADB_KEYBOARD_IME])
            time.sleep(1.0)

        # 3) 屏幕尺寸(用于聚焦/发送键定位)
        width, height = 1080, 2400
        try:
            shot = dev.get_screenshot(timeout=5)
            if getattr(shot, "width", 0) and getattr(shot, "height", 0):
                width, height = int(shot.width), int(shot.height)
        except Exception:  # noqa: BLE001
            pass

        # 4) 点输入框重新聚焦(关键:切 IME 后需重新聚焦 ADB IME 才生效)
        _sh(["input", "tap", str(int(width * 0.40)), str(int(height * 0.91))])
        time.sleep(0.8)

        # 5) 清空输入框
        _sh(["am", "broadcast", "-a", "ADB_CLEAR_TEXT"])
        time.sleep(0.5)

        # 6) 输入文本(base64,避免空串/特殊字符问题)
        encoded = base64.b64encode(text.encode("utf-8")).decode("utf-8")
        ri = _sh(["am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", encoded])
        typed = "result=0" in (ri.stdout + ri.stderr) or ri.returncode == 0
        time.sleep(0.8)

        # 7) 点发送键:优先传入坐标,否则微信稳定位置(右下角)
        if send_button and "x" in send_button and "y" in send_button:
            sx, sy = int(send_button["x"]), int(send_button["y"])
            send_method = "coordinate"
        else:
            sx, sy = int(width * 0.90), int(height * 0.89)
            send_method = "default_pos"
        _sh(["input", "tap", str(sx), str(sy)])
        sent = typed  # 只有输入成功后点发送才视为已发送
        time.sleep(0.8)
    except Exception:  # noqa: BLE001
        sent = False
        send_method = "error"
    finally:
        # 8) 恢复原输入法,让真人能继续打字。
        # 若原始 IME 为空或本身残留在 ADB Keyboard(上次异常中断),
        # 则回退到设备上第一个可用的非 ADB 输入法。
        target_ime = ""
        if original_ime and _ADB_KEYBOARD_IME not in original_ime:
            target_ime = original_ime
        else:
            try:
                lr = _sh(["ime", "list", "-s"])
                for line in (lr.stdout + lr.stderr).splitlines():
                    ime = line.strip()
                    if ime and _ADB_KEYBOARD_IME not in ime:
                        target_ime = ime
                        break
            except Exception:  # noqa: BLE001
                target_ime = ""
        if target_ime:
            try:
                _sh(["ime", "set", target_ime])
                restored_ime = target_ime
            except Exception:  # noqa: BLE001
                restored_ime = ""

    return {
        "typed": typed,
        "sent": sent,
        "send_method": send_method,
        "restored_ime": restored_ime,
    }


async def send_reply(
    device_id: str,
    text: str,
    *,
    send_button: dict[str, int] | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    contact_id: str | None = None,
) -> dict[str, Any]:
    """
    把选定话术输入聊天框并发送(实测验证过的稳定序列)。

    序列: 记录当前输入法 -> 切到 ADB Keyboard -> 点输入框重新聚焦 ->
    清空 -> ADB_INPUT_B64 输入文本 -> 点发送键 -> 恢复原输入法。
    发送键坐标: 优先用 send_button; 否则用微信稳定位置(右下角,按屏幕比例)。
    发送后必须恢复原输入法,避免 ADB Keyboard 一直开着导致真人无法打字。
    返回 send_method(coordinate/default_pos/none) 与 restored_ime 便于观测。
    """
    mgr = MobileDeviceManager()
    dev = mgr.get_device(device_id)
    adb_id = mgr.resolve_adb_device_id(device_id)

    result = await asyncio.to_thread(
        _do_send_sequence, dev, adb_id, text, send_button
    )
    typed = result["typed"]
    sent = result["sent"]
    send_method = result["send_method"]
    restored_ime = result["restored_ime"]

    if project_id:
        try:
            from api.db.mongodb import get_db
            from api.dao import mobile_artifacts as ma_dao

            await ma_dao.log_operation(
                get_db(),
                operation_type="chat_send",
                project_id=project_id,
                task_id=task_id,
                contact_id=contact_id,
                device_id=device_id,
                action="send_reply" if sent else "type_reply",
                data={
                    "text": text,
                    "typed": typed,
                    "sent": sent,
                    "send_method": send_method,
                    "send_button": send_button,
                    "restored_ime": restored_ime,
                },
            )
        except Exception:
            pass
    return {
        "ok": True,
        "typed": typed,
        "sent": sent,
        "send_method": send_method,
        "restored_ime": restored_ime,
    }
