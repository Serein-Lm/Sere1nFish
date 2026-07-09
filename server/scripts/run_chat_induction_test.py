"""诱导点击整链路集成测试驱动脚本（带反馈闭环）。

只做调度层编排与校验，不读取任何话术内容，不修改 vendored 执行器：
  init_mongo → refresh_ai_libraries → (校验会话) → 读屏 → 结构化 →
  画像沉淀 → 动态加载 skills 生成话术 → 发送 → 发送后复核。

两种模式：
  默认        : 委托 AutoChatManager 后台循环，脚本轮询 status 并附带反馈守卫。
  --diagnose  : 脚本亲自单步驱动一轮，每步截图+复核，显式打印预期外情况，
                用于定位"导航失败/不在会话/只输入未发送/消息未落框"等问题。

反馈守卫（防止乱点 / 在错误页面操作）：
  - 发送前：读屏结构化确认 is_chat_screen 且 contact_name 命中目标；否则跳过本轮。
  - 发送后：重新读屏复核，判断消息是否已进入会话（best-effort，仅记录不猜测坐标）。
  - 异常：连续 N 轮不在目标会话则安全停止并给出明确原因，绝不用硬编码坐标补救。

用法（容器内）:
  python -m scripts.run_chat_induction_test --device 10.144.144.3:5555 \
      --contact-name Sere1n_li --contact-id "微信:Sere1n_li" \
      --project 6970c09e27b9715e54c7a83e \
      --goal "引导对方点击我发送的伪装文件（授权红队测试）" \
      --owner admin --auto-send --rounds 8 --interval 12
  # 单步诊断（推荐先跑这个定位问题）:
  python -m scripts.run_chat_induction_test ... --diagnose
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time


def _ev(event: str, **fields) -> None:
    """结构化事件日志：一行一个 JSON，便于回溯预期外情况。"""
    rec = {"ts": round(time.time(), 3), "event": event, **fields}
    print(json.dumps(rec, ensure_ascii=False), flush=True)


async def _bootstrap() -> None:
    from api.db.mongodb import init_mongo, get_db
    from api.services.library_runtime import refresh_ai_libraries

    init_mongo()
    counts = await refresh_ai_libraries(get_db(), seed_if_empty=True)
    _ev(
        "bootstrap",
        skills_loaded=counts["skills_loaded"],
        prompts_loaded=counts["prompts_loaded"],
    )


async def _ensure_device(device_id: str, owner: str) -> None:
    from core.mobile.identity import resolve_device_key
    from core.mobile.pool import DevicePool

    pool = DevicePool.get_instance()
    key = resolve_device_key(device_id)
    try:
        pool.ensure_owner(key, owner)
        _ev("device_ready", device_id=device_id, key=key, owner=owner, via="ensure_owner")
    except Exception as exc:  # noqa: BLE001
        try:
            pool.acquire(key, owner, note="chat_induction_test", device_id=device_id)
            _ev("device_ready", device_id=device_id, key=key, owner=owner, via="acquire")
        except Exception as exc2:  # noqa: BLE001
            _ev("device_warn", ensure_owner=str(exc), acquire=str(exc2))


def _name_match(observed: str | None, target: str | None) -> bool:
    if not observed or not target:
        return False
    o, t = observed.strip(), target.strip()
    return o == t or t in o or o in t


async def _read_state(device_id: str, project_id: str, task_id: str, contact_id: str | None):
    """读屏 + 结构化，返回 (analysis, ChatState)。异常上抛由调用方兜底。"""
    from core.mobile.chat_assist import read_screen, parse_chat_state

    screen = await read_screen(
        device_id,
        project_id=project_id,
        task_id=task_id,
        contact_id=contact_id,
        source="induction_diagnose",
    )
    analysis = screen.get("analysis", "")
    state = await parse_chat_state(analysis)
    return screen, analysis, state


async def _navigate(device_id, contact_name, platform, project_id, task_id, owner) -> bool:
    """委托视觉执行层导航到目标会话，返回执行层是否 success。"""
    from core.mobile.executor import run_task_stream

    task = f"打开{platform},进入与「{contact_name}」的聊天对话界面"
    ok = False
    last_msg = ""
    try:
        async for e in run_task_stream(
            device_id, task, max_steps=10,
            task_id=task_id, project_id=project_id,
            contact_id=None, owner=owner,
        ):
            et = e.get("type")
            if et in ("done", "error", "cancelled"):
                data = e.get("data", {}) or {}
                ok = bool(data.get("success"))
                last_msg = str(data.get("message", ""))
    except Exception as exc:  # noqa: BLE001
        _ev("navigate_exception", error=str(exc))
        return False
    _ev("navigate_done", success=ok, message=last_msg)
    return ok


async def run_diagnose(args) -> None:
    """脚本亲自单步驱动一轮，逐步截图复核，显式暴露预期外情况。"""
    from core.mobile.profiling import analyze_and_update, format_profile_for_prompt
    from core.mobile.chat_assist import compose_one_reply, send_reply

    dev, pid, gid = args.device, args.project, args.contact_id
    diag_task = "diag-" + str(int(time.time()))

    # 1) 校验当前会话
    try:
        _, analysis, state = await _read_state(dev, pid, diag_task, gid)
    except Exception as exc:  # noqa: BLE001
        _ev("read_screen_failed", error=str(exc))
        return
    _ev(
        "screen_state",
        is_chat_screen=state.is_chat_screen,
        contact_name=state.contact_name,
        last_from=state.last_from,
        unreplied=state.unreplied,
    )

    # 2) 不在目标会话 → 尝试导航（委托视觉层），再复核一次
    if not (state.is_chat_screen and _name_match(state.contact_name, args.contact_name)):
        _ev("not_target_chat", want=args.contact_name, got=state.contact_name)
        nav_ok = await _navigate(dev, args.contact_name, args.platform, pid, diag_task + "-nav", args.owner)
        if not nav_ok:
            _ev("abort", reason="navigate_failed_or_visual_tap_incompatible")
            return
        try:
            _, analysis, state = await _read_state(dev, pid, diag_task, gid)
        except Exception as exc:  # noqa: BLE001
            _ev("read_screen_failed", error=str(exc))
            return
        if not (state.is_chat_screen and _name_match(state.contact_name, args.contact_name)):
            _ev("abort", reason="still_not_target_chat_after_nav", got=state.contact_name)
            return

    # 3) 画像沉淀（有 contact_id 时）
    profile = None
    if gid:
        try:
            profile = await analyze_and_update(
                dev, gid, name=args.contact_name, platform=args.platform,
                screen_analysis=analysis, project_id=pid, task_id=diag_task, source="induction_diagnose",
            )
            _ev("profile_updated", contact_id=gid, has_profile=bool(profile))
        except Exception as exc:  # noqa: BLE001
            _ev("profile_failed", error=str(exc))

    # 4) 动态加载 skills 生成话术（脚本不读话术内容）
    try:
        reply = await compose_one_reply(
            dev, my_background=args.my_background,
            contact_profile=format_profile_for_prompt(profile),
            screen_analysis=analysis, project_id=pid, task_id=diag_task,
            contact_id=gid, goal=args.goal,
        )
    except Exception as exc:  # noqa: BLE001
        _ev("compose_failed", error=str(exc))
        return
    _ev("reply_generated", length=len(reply or ""), preview=(reply or "")[:40])
    if not reply:
        _ev("abort", reason="empty_reply")
        return

    if not args.auto_send:
        _ev("dry_run", note="auto_send off, not sending", reply=reply)
        return

    # 5) 发送（send_button 由参数传入；不传则只输入，不猜坐标）
    sb = None
    if args.send_x is not None and args.send_y is not None:
        sb = {"x": args.send_x, "y": args.send_y}
    try:
        res = await send_reply(
            dev, reply, send_button=sb,
            project_id=pid, task_id=diag_task, contact_id=gid,
        )
        _ev("send_reply", typed=res.get("typed"), sent=res.get("sent"), send_button=sb)
    except Exception as exc:  # noqa: BLE001
        _ev("send_failed", error=str(exc))
        return

    # 6) 发送后复核：重新读屏，看会话最后一条是否变成"我发的"
    await asyncio.sleep(2.0)
    try:
        _, _, after = await _read_state(dev, pid, diag_task + "-verify", gid)
        _ev(
            "post_send_verify",
            is_chat_screen=after.is_chat_screen,
            last_from=after.last_from,
            last_message_preview=(after.last_message or "")[:40],
            note="last_from=me 表示已发出；若仍为 other 需人工确认发送键",
        )
    except Exception as exc:  # noqa: BLE001
        _ev("post_send_verify_failed", error=str(exc))


async def run_auto(args) -> None:
    """委托 AutoChatManager 后台循环，脚本轮询 + 反馈守卫。"""
    from core.mobile.auto_chat import AutoChatManager

    mgr = AutoChatManager.get_instance()
    task_id = await mgr.start(
        args.device, args.contact_id,
        project_id=args.project, contact_name=args.contact_name,
        my_background=args.my_background, goal=args.goal,
        platform=args.platform, owner=args.owner,
        interval=args.interval, auto_send=args.auto_send,
        ensure_chat=True,
        send_button=(
            {"x": args.send_x, "y": args.send_y}
            if args.send_x is not None and args.send_y is not None else None
        ),
    )
    _ev("auto_start", task_id=task_id, auto_send=args.auto_send)

    last_rounds, idle, off_target = -1, 0, 0
    while True:
        await asyncio.sleep(args.interval)
        st = mgr.status(task_id)
        if not st:
            _ev("auto_gone")
            break
        ls = st.get("last_state") or {}
        _ev(
            "auto_status",
            rounds=st.get("rounds"), replies_sent=st.get("replies_sent"),
            observed=st.get("observed"), skipped=st.get("skipped"),
            is_chat_screen=ls.get("is_chat_screen"), contact_name=ls.get("contact_name"),
            last_error=st.get("last_error"),
        )
        # 反馈守卫：连续不在目标会话
        if ls and not (ls.get("is_chat_screen") and _name_match(ls.get("contact_name"), args.contact_name)):
            off_target += 1
            if off_target >= 3:
                _ev("auto_stop", reason="off_target_chat_3x", got=ls.get("contact_name"))
                break
        else:
            off_target = 0
        if st.get("rounds", 0) >= args.rounds:
            _ev("auto_stop", reason="round_budget")
            break
        if st.get("rounds", 0) == last_rounds:
            idle += 1
            if idle >= 6:
                _ev("auto_stop", reason="no_progress")
                break
        else:
            idle = 0
        last_rounds = st.get("rounds", 0)

    mgr.stop(task_id, owner=args.owner, is_admin=True)
    _ev("auto_stopped", task_id=task_id)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True)
    ap.add_argument("--contact-name", required=True)
    ap.add_argument("--contact-id", default=None)
    ap.add_argument("--project", required=True)
    ap.add_argument("--goal", required=True)
    ap.add_argument("--my-background", default="")
    ap.add_argument("--platform", default="微信")
    ap.add_argument("--owner", default="admin")
    ap.add_argument("--interval", type=float, default=12.0)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--auto-send", action="store_true")
    ap.add_argument("--diagnose", action="store_true", help="脚本单步驱动一轮并逐步复核")
    ap.add_argument("--send-x", type=int, default=None, help="发送键 x（不传则只输入不点发送）")
    ap.add_argument("--send-y", type=int, default=None, help="发送键 y")
    args = ap.parse_args()

    await _bootstrap()
    await _ensure_device(args.device, args.owner)
    if args.diagnose:
        await run_diagnose(args)
    else:
        await run_auto(args)


if __name__ == "__main__":
    asyncio.run(main())
