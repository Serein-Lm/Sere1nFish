"""WeChat 结果页守卫：用确定性 activity 校验代替盲按返回，防止采集跳出微信。

背景（修复的 bug）：详情采集结束后 `_restore_results_page` 只盲按一次返回，
且候选点击前从不校验当前页面。一旦某次点击打开了视频号/小程序/外部浏览器、
或剪贴板桥（系统设置）恢复不彻底，后续候选的坐标就落在错误页面上，
错误页面上继续点击 + 盲返回会把流程一路带出微信（搜索页→微信主界面→桌面）。

守卫规则（全部确定性，无视觉模型参与）：
- 已在搜索结果页（RESULT_ACTIVITY）→ 无需任何按键。
- 在微信内的文章/菜单/视频等中间页 → 有界按返回（≤max_backs），每按一次重新校验。
- 在搜索输入页（SEARCH_ACTIVITY）或微信主界面（LAUNCHER_ACTIVITY）→ 立即停手：
  这两处再按返回就会离开搜索流程/退出微信，宁可让本轮关键词自然结束，
  由下一个关键词的确定性导航自愈。
- 不在微信包内（已被带出微信）→ 立即停手，绝不在其他应用里按返回。
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

WECHAT_PACKAGE = "com.tencent.mm"
SEARCH_ACTIVITY = "com.tencent.mm.plugin.fts.ui.FTSMainUI"
RESULT_ACTIVITY = (
    "com.tencent.mm.plugin.webview.ui.tools.fts.MMFTSSOSHomeWebViewUI"
)
LAUNCHER_ACTIVITY = "com.tencent.mm.ui.LauncherUI"

# 搜索输入页与微信主界面：从这里再按返回就会跳出搜索流程/退出微信
_NEVER_BACK_ACTIVITIES = frozenset({SEARCH_ACTIVITY, LAUNCHER_ACTIVITY})

_COMPONENT_RE = re.compile(
    r"(?:mCurrentFocus|mFocusedApp)=.*?\s+"
    r"([A-Za-z0-9_.]+)"
    r"/"
    r"([A-Za-z0-9_.$]+|\.?[A-Za-z0-9_.$]+)"
)

CommandRunner = Callable[[list, int], subprocess.CompletedProcess]


def _default_command_runner(
    args: list[str], timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["adb", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@dataclass(frozen=True, slots=True)
class WechatPageGuardResult:
    """守卫执行结果。restored=True 表示当前确在搜索结果页。"""

    restored: bool
    action: str
    activity: str = ""
    package: str = ""
    backs: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "restored": self.restored,
            "action": self.action,
            "activity": self.activity,
            "package": self.package,
            "backs": self.backs,
        }


def read_wechat_component(
    adb_device_id: str,
    *,
    runner: CommandRunner = _default_command_runner,
) -> tuple[str, str]:
    """读取前台 (package, activity)；读取失败返回 ("", "")。"""
    try:
        result = runner(
            ["-s", adb_device_id, "shell", "dumpsys", "window"],
            8,
        )
    except Exception:  # noqa: BLE001
        return "", ""
    if result.returncode != 0:
        return "", ""
    match = _COMPONENT_RE.search(result.stdout or "")
    if not match:
        return "", ""
    package = match.group(1)
    activity = match.group(2)
    if activity.startswith("."):
        activity = f"{package}{activity}"
    return package, activity


def ensure_wechat_results_page(
    adb_device_id: str,
    *,
    runner: CommandRunner = _default_command_runner,
    back_action: Callable[[str], None] | None = None,
    max_backs: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> WechatPageGuardResult:
    """确保手机停在微信搜索结果页；只在安全的中间页上有界按返回。

    不会按返回的位置：搜索输入页、微信主界面、微信包外。
    """
    backs = 0

    def press_back() -> bool:
        nonlocal backs
        if back_action is None:
            try:
                runner(["-s", adb_device_id, "shell", "input", "keyevent", "4"], 5)
            except Exception:  # noqa: BLE001
                return False
        else:
            try:
                back_action(adb_device_id)
            except Exception:  # noqa: BLE001
                return False
        backs += 1
        sleep(0.5)
        return True

    for _attempt in range(max(1, max_backs) + 1):
        package, activity = read_wechat_component(
            adb_device_id, runner=runner
        )
        if activity == RESULT_ACTIVITY and package == WECHAT_PACKAGE:
            return WechatPageGuardResult(
                restored=True,
                action="already_results" if backs == 0 else "backed_to_results",
                activity=activity,
                package=package,
                backs=backs,
            )
        if package != WECHAT_PACKAGE:
            return WechatPageGuardResult(
                restored=False,
                action="left_wechat",
                activity=activity,
                package=package,
                backs=backs,
            )
        if activity in _NEVER_BACK_ACTIVITIES:
            return WechatPageGuardResult(
                restored=False,
                action=(
                    "stopped_at_search_input"
                    if activity == SEARCH_ACTIVITY
                    else "stopped_at_wechat_home"
                ),
                activity=activity,
                package=package,
                backs=backs,
            )
        if backs >= max_backs:
            break
        if not press_back():
            break
    package, activity = read_wechat_component(adb_device_id, runner=runner)
    return WechatPageGuardResult(
        restored=activity == RESULT_ACTIVITY and package == WECHAT_PACKAGE,
        action="restored_after_backs" if activity == RESULT_ACTIVITY else "results_lost",
        activity=activity,
        package=package,
        backs=backs,
    )


__all__ = [
    "LAUNCHER_ACTIVITY",
    "RESULT_ACTIVITY",
    "SEARCH_ACTIVITY",
    "WECHAT_PACKAGE",
    "WechatPageGuardResult",
    "ensure_wechat_results_page",
    "read_wechat_component",
]
