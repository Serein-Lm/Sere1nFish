from __future__ import annotations

import subprocess

from core.mobile.app_launcher import AdbAppLauncher


def _completed(args: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")


class _SingleAppRunner:
    def __call__(self, args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        del timeout
        if "resolve-activity" in args:
            return _completed(args, "com.tencent.mm/.ui.LauncherUI\n")
        if "am" in args and "start" in args:
            return _completed(args, "Starting: Intent\n")
        if args[-3:] == ["dumpsys", "activity", "activities"]:
            return _completed(
                args,
                "topResumedActivity=ActivityRecord{abc u0 "
                "com.tencent.mm/.ui.LauncherUI t1}\n",
            )
        raise AssertionError(f"unexpected command: {args}")


class _DualAppRunner:
    def __init__(self) -> None:
        self.foreground = "com.xingin.xhs"
        self.tap: tuple[int, int] | None = None
        self.system_panels_collapsed = False

    def __call__(self, args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        del timeout
        if "resolve-activity" in args:
            return _completed(args, "com.tencent.mm/.ui.LauncherUI\n")
        if args[-3:] == ["cmd", "statusbar", "collapse"]:
            self.system_panels_collapsed = True
            return _completed(args)
        if "am" in args and "start" in args:
            self.foreground = "com.android.intentresolver"
            return _completed(args, "Starting: Intent\n")
        if args[-3:] == ["dumpsys", "activity", "activities"]:
            return _completed(
                args,
                "topResumedActivity=ActivityRecord{abc u0 "
                f"{self.foreground}/.ChooserActivity t1}}\n",
            )
        if "uiautomator" in args:
            return _completed(
                args,
                "<?xml version='1.0'?><hierarchy>"
                "<node clickable='false' bounds='[0,0][400,400]'>"
                "<node clickable='true' bounds='[0,100][200,300]'>"
                "<node text='微信' clickable='false' bounds='[70,180][130,220]'/>"
                "</node>"
                "<node clickable='true' bounds='[200,100][400,300]'>"
                "<node text='微信(分身)' clickable='false' bounds='[250,180][350,220]'/>"
                "</node>"
                "</node></hierarchy>",
            )
        if "input" in args and "tap" in args:
            self.tap = (int(args[-2]), int(args[-1]))
            self.foreground = "com.tencent.mm"
            return _completed(args)
        raise AssertionError(f"unexpected command: {args}")


def test_launch_verifies_single_app_foreground() -> None:
    launcher = AdbAppLauncher(runner=_SingleAppRunner(), sleep=lambda _: None)

    result = launcher.launch("device-1", "微信")

    assert result.ok is True
    assert result.foreground_package == "com.tencent.mm"
    assert result.chooser_handled is False


def test_launch_selects_primary_app_from_dual_app_chooser() -> None:
    runner = _DualAppRunner()
    launcher = AdbAppLauncher(runner=runner, sleep=lambda _: None)

    result = launcher.launch("device-1", "微信")

    assert result.ok is True
    assert result.chooser_handled is True
    assert result.selected_instance == "primary"
    assert runner.tap == (100, 200)
    assert runner.system_panels_collapsed is True


def test_launch_can_select_cloned_app_from_dual_app_chooser() -> None:
    runner = _DualAppRunner()
    launcher = AdbAppLauncher(runner=runner, sleep=lambda _: None)

    result = launcher.launch("device-1", "微信", instance="clone")

    assert result.ok is True
    assert result.chooser_handled is True
    assert result.selected_instance == "clone"
    assert runner.tap == (300, 200)


def test_launch_rejects_unknown_app() -> None:
    launcher = AdbAppLauncher(runner=_SingleAppRunner(), sleep=lambda _: None)

    result = launcher.launch("device-1", "不存在的应用")

    assert result.ok is False
    assert result.error == "不支持的应用: 不存在的应用"
