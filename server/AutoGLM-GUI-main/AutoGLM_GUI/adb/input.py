"""Input utilities for Android device text input."""

import base64
import subprocess

from AutoGLM_GUI.platform_utils import build_adb_command
from AutoGLM_GUI.trace import trace_span

_ADB_INPUT_TIMEOUT = 10


def _run_adb_input_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=_ADB_INPUT_TIMEOUT,
    )
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(output or f"adb command failed with code {result.returncode}")
    return result


def type_text(text: str, device_id: str | None = None) -> None:
    adb_prefix = build_adb_command(device_id)

    with trace_span(
        "adb.type_text",
        attrs={"device_id": device_id, "text_length": len(text)},
    ):
        if not text:
            _run_adb_input_command(
                adb_prefix + ["shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"]
            )
            return
        encoded_text = base64.b64encode(text.encode("utf-8")).decode("utf-8")
        _run_adb_input_command(
            adb_prefix
            + [
                "shell",
                "am",
                "broadcast",
                "-a",
                "ADB_INPUT_B64",
                "--es",
                "msg",
                encoded_text,
            ]
        )


def clear_text(device_id: str | None = None) -> None:
    adb_prefix = build_adb_command(device_id)

    with trace_span("adb.clear_text", attrs={"device_id": device_id}):
        _run_adb_input_command(
            adb_prefix + ["shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"]
        )


def detect_and_set_adb_keyboard(device_id: str | None = None) -> str:
    adb_prefix = build_adb_command(device_id)

    with trace_span(
        "adb.detect_adb_keyboard",
        attrs={"device_id": device_id},
    ):
        result = _run_adb_input_command(
            adb_prefix + ["shell", "settings", "get", "secure", "default_input_method"],
        )
    current_ime = (result.stdout + result.stderr).strip()

    if "com.android.adbkeyboard/.AdbIME" not in current_ime:
        with trace_span(
            "adb.set_adb_keyboard",
            attrs={"device_id": device_id},
        ):
            _run_adb_input_command(
                adb_prefix + ["shell", "ime", "set", "com.android.adbkeyboard/.AdbIME"],
            )

    type_text("", device_id)

    return current_ime


def restore_keyboard(ime: str, device_id: str | None = None) -> None:
    adb_prefix = build_adb_command(device_id)

    with trace_span(
        "adb.restore_keyboard",
        attrs={"device_id": device_id},
    ):
        _run_adb_input_command(adb_prefix + ["shell", "ime", "set", ime])
