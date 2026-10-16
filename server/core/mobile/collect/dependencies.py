"""Device prerequisites; uncertain screens stay ordinary navigation failures."""
from core.task_dependencies import TaskDependencyBlocked


_WECHAT_AUTH_ACTIVITIES = frozenset({
    "com.tencent.mm.ui.account.LoginUI",
    "com.tencent.mm.ui.account.WelcomeActivity",
    "com.tencent.mm.ui.account.mobile.MobileInputUI",
    "com.tencent.mm.ui.account.mobile.MobileLoginOrForceReg",
    "com.tencent.mm.ui.account.RegByMobileVerifySmsUI",
})


def require_authenticated_activity(activity: str) -> None:
    if activity in _WECHAT_AUTH_ACTIVITIES:
        raise TaskDependencyBlocked(
            "needs_auth", "微信需要人工完成登录后继续任务",
            context={"activity": activity, "source": "wechat"},
        )
