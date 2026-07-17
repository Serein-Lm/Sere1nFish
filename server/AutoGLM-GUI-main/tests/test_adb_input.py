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
