"""Fast mobile command compilation and execution.

This module is intentionally below the HTTP layer. It gives the mobile AI
execution stack a deterministic tool path for obvious commands before falling
back to the slower visual reasoning loop.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from AutoGLM_GUI.adb.apps import APP_PACKAGES

from core.mobile.app_launcher import AdbAppLauncher
from core.mobile.coordinates import resolve_swipe
from core.mobile.manager import MobileDeviceManager


_OPEN_RE = re.compile(r"(打开|启动|进入|运行|拉起)")
_BROWSE_RE = re.compile(r"(浏览|逛|刷|看看|看一看|推荐|首页推荐|信息流)")
_HOME_RE = re.compile(r"(回到桌面|返回桌面|主屏幕|回首页|回到主页)")
_BACK_RE = re.compile(r"(返回上一页|返回上页|后退|返回)")
_UNLOCK_RE = re.compile(r"(解锁|唤醒并解锁|亮屏并解锁)")
_WAKE_RE = re.compile(r"(唤醒|亮屏|点亮屏幕)")
_COMPLEX_INTENT_RE = re.compile(
    r"(搜索|查找|找到|点击|点开|选择|输入|填写|发送|发消息|回复|聊天|联系人|"
    r"订单|购物车|加购|购买|下单|支付|付款|地址|筛选|排序|详情|收藏|关注|"
    r"点赞|评论|私信|登录|注册|扫码|拍照|上传|下载)"
)
_SPACE_RE = re.compile(r"\s+")

_SYSTEM_APP_ALIASES: dict[str, str] = {
    "设置": "Settings",
    "系统设置": "Settings",
}


@dataclass(frozen=True)
class MobileAction:
    """One deterministic mobile action."""

    kind: str
    label: str
    args: dict[str, Any] = field(default_factory=dict)

    def to_public_action(self) -> dict[str, Any]:
        return {"action": self.kind, **self.args}


def _normalize_task(task: str) -> str:
    return _SPACE_RE.sub("", task.strip().lower())


def _find_app_name(task: str) -> str | None:
    normalized = _normalize_task(task)
    for app_name in sorted(APP_PACKAGES, key=len, reverse=True):
        if _normalize_task(app_name) in normalized:
            return app_name
    for alias, app_name in _SYSTEM_APP_ALIASES.items():
        if alias in normalized and app_name in APP_PACKAGES:
            return app_name
    return None


def compile_mobile_actions(task: str) -> list[MobileAction] | None:
    """Compile obvious mobile tasks to deterministic device actions.

    Returns ``None`` when the task still needs visual/LLM reasoning.
    """
    normalized = _normalize_task(task)
    if not normalized:
        return None

    actions: list[MobileAction] = []
    app_name = _find_app_name(task)
    wants_open = bool(_OPEN_RE.search(normalized))
    wants_browse = bool(_BROWSE_RE.search(normalized))

    if "设置" in normalized and app_name != "Settings":
        return None

    if _COMPLEX_INTENT_RE.search(normalized):
        return None

    if _UNLOCK_RE.search(normalized):
        return [MobileAction("wake_unlock", "唤醒并解锁屏幕")]

    if _WAKE_RE.search(normalized):
        return [MobileAction("wake", "唤醒屏幕")]

    if app_name and (wants_open or wants_browse):
        actions.append(
            MobileAction(
                "launch_app",
                f"打开 {app_name}",
                {"app_name": app_name},
            )
        )
        if not wants_browse:
            return actions
        actions.append(MobileAction("wait", "等待应用加载", {"seconds": 1.2}))

    if wants_browse:
        actions.extend(
            [
                MobileAction(
                    "swipe",
                    "浏览推荐内容",
                    {
                        "start_x": 500,
                        "start_y": 780,
                        "end_x": 500,
                        "end_y": 260,
                        "duration_ms": 450,
                    },
                ),
                MobileAction("wait", "等待内容刷新", {"seconds": 0.4}),
                MobileAction(
                    "swipe",
                    "继续浏览推荐内容",
                    {
                        "start_x": 500,
                        "start_y": 780,
                        "end_x": 500,
                        "end_y": 260,
                        "duration_ms": 450,
                    },
                ),
            ]
        )
        return actions

    if _HOME_RE.search(normalized):
        return [MobileAction("home", "回到主屏幕")]

    if _BACK_RE.search(normalized):
        return [MobileAction("back", "返回上一页")]

    return None


def describe_compiled_actions(actions: list[MobileAction]) -> list[str]:
    return [action.label for action in actions]


def _execute_action(device_id: str, action: MobileAction) -> tuple[bool, str]:
    mgr = MobileDeviceManager()
    dev = mgr.get_device(device_id)
    adb_device_id = mgr.resolve_adb_device_id(device_id)

    if action.kind == "launch_app":
        result = AdbAppLauncher().launch(
            adb_device_id,
            str(action.args["app_name"]),
            instance=(
                "clone" if action.args.get("app_instance") == "clone" else "primary"
            ),
        )
        return (
            result.ok,
            "应用已进入前台"
            if result.ok
            else f"应用启动失败: {result.error or '未进入前台'}",
        )

    if action.kind == "wait":
        seconds = float(action.args.get("seconds", 0.5))
        time.sleep(max(0.0, min(seconds, 10.0)))
        return True, "已等待"

    if action.kind == "swipe":
        sx, sy, ex, ey = resolve_swipe(
            int(action.args.get("start_x", 500)),
            int(action.args.get("start_y", 780)),
            int(action.args.get("end_x", 500)),
            int(action.args.get("end_y", 260)),
            device_id=adb_device_id,
            coord_space="normalized_1000",
        )
        dev.swipe(
            sx,
            sy,
            ex,
            ey,
            int(action.args.get("duration_ms", 450)),
            delay=0.1,
        )
        return True, "已滑动"

    if action.kind == "home":
        dev.home(delay=0.1)
        return True, "已返回主屏幕"

    if action.kind == "back":
        dev.back(delay=0.1)
        return True, "已返回上一页"

    if action.kind == "wake":
        from core.mobile.pool import DevicePool

        result = DevicePool.get_instance().wake(device_id, stay_on=True)
        return bool(result.get("ok")), "已唤醒屏幕" if result.get("ok") else str(result)

    if action.kind == "wake_unlock":
        from core.mobile.pool import DevicePool

        result = DevicePool.get_instance().wake_and_unlock(device_id, stay_on=True)
        return (
            bool(result.get("ok")),
            "已唤醒并尝试解锁" if result.get("ok") else str(result),
        )

    return False, f"未知动作: {action.kind}"


async def run_compiled_actions_stream(
    device_id: str,
    task: str,
    actions: list[MobileAction],
    *,
    task_id: str | None = None,
    project_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run compiled actions and yield standard agent/task frames."""
    task_id = task_id or uuid.uuid4().hex[:12]
    yield {
        "type": "task_start",
        "data": {
            "task_id": task_id,
            "project_id": project_id,
            "device_id": device_id,
            "task": task,
            "mode": "compiled_tools",
        },
    }

    success = True
    final_message = "任务完成"
    executed_steps = 0
    for index, action in enumerate(actions, start=1):
        ok, message = await asyncio.to_thread(_execute_action, device_id, action)
        executed_steps = index
        success = success and ok
        final_message = message
        yield {
            "type": "step",
            "data": {
                "step": index,
                "thinking": "",
                "action": action.to_public_action(),
                "success": ok,
                "finished": False,
                "message": message,
            },
        }
        if not ok:
            break

    yield {
        "type": "done",
        "data": {
            "message": final_message if success else f"执行失败: {final_message}",
            "steps": executed_steps,
            "success": success,
            "mode": "compiled_tools",
        },
    }
