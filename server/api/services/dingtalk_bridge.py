"""钉钉群 @机器人 ↔ AI 中枢 桥接 service。

这是「IM 提问 → AI 中枢回答 → 回推来源会话」这一业务语义的唯一收敛点：
- 入站验签、消息解析、来源会话回推都在这一层；
- AI 中枢的调用只通过统一执行入口 execute_stream(workflow="assistant", ...)，
  与 Web 端 /agent/stream 走同一条链路；
- 以后 AI 中枢的工作流/工具/编排如何演进，本桥接层无需改动，
  只有当回推格式或来源渠道变化时才动这里。

约束：
- 后台任务用 core.background.spawn_background，避免裸 create_task 被 GC；
- token 归因用 core.observability.observation_context 包裹 AI 调用；
- 不直接 import 具体通道细节以外的东西，通道细节收敛在 crawler_tools.dingtalk_bot。
"""
from __future__ import annotations

from typing import Any

from core.background import spawn_background
from core.logger import get_logger

logger = get_logger("api.services.dingtalk_bridge")

# 钉钉提问默认走的 AI 中枢工作流（携带全部工具，与 Web 端一致）
_HUB_WORKFLOW = "assistant"
# 回复文本长度上限，避免超过钉钉消息容量
_REPLY_MAX_CHARS = 4000


def _extract_final_text(event: dict[str, Any], sections: dict[str, str]) -> None:
    """从 SSE 事件中累积最终回复文本（按 section 归并）。

    与 api/routers/agent.py 的同名逻辑保持一致，确保钉钉与 Web 端取到同样的结果文本。
    """
    if event.get("event") != "final":
        return
    data = event.get("data") or {}
    section = str(data.get("section") or "_default")
    content = data.get("content")
    if content:
        sections[section] = str(content)


def _parse_inbound(payload: dict[str, Any]) -> dict[str, Any]:
    """解析钉钉 outgoing 回调 body，提取问答桥接需要的字段。"""
    text = ((payload.get("text") or {}).get("content") or "").strip()
    return {
        "query": text,
        "session_webhook": (payload.get("sessionWebhook") or "").strip(),
        "sender_nick": (payload.get("senderNick") or "").strip(),
        "sender_staff_id": (payload.get("senderStaffId") or "").strip(),
        "conversation_id": (payload.get("conversationId") or "").strip(),
        "conversation_title": (payload.get("conversationTitle") or "").strip(),
    }


async def handle_inbound(payload: dict[str, Any], timestamp: str, sign: str) -> dict[str, Any]:
    """处理一条钉钉入站回调。

    流程：验签 → 解析 → 立即返回（由路由 ack）→ 后台跑 hub 并回推。

    Returns:
        dict：{"accepted": bool, "reason": str}，供路由记录，不作为钉钉响应体。
    """
    from api.dao import config as config_dao
    from api.db.mongodb import get_db
    from crawler_tools.dingtalk_bot import verify_inbound_signature

    db = get_db()
    dingtalk_cfg = await config_dao.get_dingtalk_config(db, "default")
    app_secret = (dingtalk_cfg or {}).get("outgoing_app_secret") or ""
    if not app_secret:
        logger.warning("钉钉入站回调：未配置 outgoing_app_secret，拒绝处理")
        return {"accepted": False, "reason": "not_configured"}

    if not verify_inbound_signature(timestamp, sign, app_secret):
        logger.warning("钉钉入站回调：签名校验失败")
        return {"accepted": False, "reason": "invalid_signature"}

    parsed = _parse_inbound(payload)
    if not parsed["query"]:
        return {"accepted": False, "reason": "empty_query"}
    if not parsed["session_webhook"]:
        logger.warning("钉钉入站回调：缺少 sessionWebhook，无法回推")
        return {"accepted": False, "reason": "no_session_webhook"}

    # hub 推理较慢；立即受理并在后台执行 + 回推，满足钉钉 5s 响应要求
    spawn_background(_run_and_reply(parsed), name="dingtalk_hub_reply")
    return {"accepted": True, "reason": "queued"}


async def _run_and_reply(parsed: dict[str, Any]) -> None:
    """后台：调用 AI 中枢生成回答，并回推到来源会话。"""
    from core.observability import observation_context
    from crawler_tools.dingtalk_bot import reply_to_session_webhook
    from Sere1nGraph.graph.workflow.executor import execute_stream
    from api.services.runtime_config import get_runtime_app_config

    query = parsed["query"]
    session_webhook = parsed["session_webhook"]
    sender_staff_id = parsed["sender_staff_id"]
    at_users = [sender_staff_id] if sender_staff_id else []

    try:
        app_config = await get_runtime_app_config()
        sections: dict[str, str] = {}
        with observation_context(
            phase="dingtalk_hub",
            agent="dingtalk",
            task_type=_HUB_WORKFLOW,
        ):
            async for event in execute_stream(
                workflow=_HUB_WORKFLOW,
                query=query,
                app_config=app_config,
                options={},
            ):
                _extract_final_text(event, sections)

        final_text = "\n\n".join(v for v in sections.values() if v).strip()
        if not final_text:
            final_text = "（本次未生成文本内容）"
        if len(final_text) > _REPLY_MAX_CHARS:
            final_text = final_text[:_REPLY_MAX_CHARS] + "\n\n…（内容过长已截断）"

        result = await reply_to_session_webhook(
            session_webhook,
            title="AI 中枢回复",
            text=final_text,
            at_user_ids=at_users,
        )
        if not result.success:
            logger.warning(f"钉钉回推失败：{result.message}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"钉钉 hub 处理异常：{exc}")
        try:
            await reply_to_session_webhook(
                session_webhook,
                title="AI 中枢",
                text=f"抱歉，处理你的问题时出错了：{exc}",
                at_user_ids=at_users,
            )
        except Exception:  # noqa: BLE001
            pass
