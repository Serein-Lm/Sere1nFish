from __future__ import annotations

import base64
import subprocess

from AutoGLM_GUI.adb import input as adb_input


def _completed(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def test_empty_text_uses_clear_broadcast(monkeypatch) -> None:
    commands: list[list[str]] = []

    def run(command: list[str]):
        commands.append(command)
        return _completed(command)

    monkeypatch.setattr(adb_input, "_run_adb_input_command", run)
    monkeypatch.setattr(adb_input, "build_adb_command", lambda _device_id: ["adb"])

    adb_input.type_text("", "device-1")

    assert commands == [["adb", "shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"]]


def test_text_uses_base64_broadcast(monkeypatch) -> None:
    commands: list[list[str]] = []

    def run(command: list[str]):
        commands.append(command)
        return _completed(command)

    monkeypatch.setattr(adb_input, "_run_adb_input_command", run)
    monkeypatch.setattr(adb_input, "build_adb_command", lambda _device_id: ["adb"])

    adb_input.type_text("天津滨海国际机场 招标", "device-1")

    encoded = base64.b64encode("天津滨海国际机场 招标".encode()).decode()
    assert commands == [
        [
            "adb",
            "shell",
            "am",
            "broadcast",
            "-a",
            "ADB_INPUT_B64",
            "--es",
            "msg",
            encoded,
        ]
    ]


def test_detect_keyboard_verifies_active_ime(monkeypatch) -> None:
    commands: list[list[str]] = []
    outputs = iter(
        [
            "vendor/.Ime",
            "Input method com.android.adbkeyboard/.AdbIME selected",
            "com.android.adbkeyboard/.AdbIME",
            "Broadcast completed: result=0",
        ]
    )

    def run(command: list[str]):
        commands.append(command)
        result = _completed(command)
        result.stdout = next(outputs)
        return result

    monkeypatch.setattr(adb_input, "_run_adb_input_command", run)
    monkeypatch.setattr(adb_input, "build_adb_command", lambda _device_id: ["adb"])
    monkeypatch.setattr(adb_input.time, "sleep", lambda _seconds: None)

    original = adb_input.detect_and_set_adb_keyboard("device-1")

    assert original == "vendor/.Ime"
    assert ["shell", "ime", "set", "com.android.adbkeyboard/.AdbIME"] == commands[1][1:]


def test_detect_keyboard_rejects_failed_switch(monkeypatch) -> None:
    outputs = iter(["vendor/.Ime", "selected", "vendor/.Ime"])

    def run(command: list[str]):
        result = _completed(command)
        result.stdout = next(outputs)
        return result

    monkeypatch.setattr(adb_input, "_run_adb_input_command", run)
    monkeypatch.setattr(adb_input, "build_adb_command", lambda _device_id: ["adb"])
    monkeypatch.setattr(adb_input.time, "sleep", lambda _seconds: None)

    try:
        adb_input.detect_and_set_adb_keyboard("device-1")
    except RuntimeError as exc:
        assert "切换未生效" in str(exc)
    else:
        raise AssertionError("输入法切换失败时必须抛错")
