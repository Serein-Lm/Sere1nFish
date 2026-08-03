from __future__ import annotations

import pytest

from core.mobile.keyboard import (
    ADB_KEYBOARD_IME,
    KeyboardRestoreError,
    MobileKeyboardService,
    choose_manual_ime,
)


def test_choose_manual_ime_prefers_previous_keyboard_and_avoids_voice() -> None:
    enabled = [
        ADB_KEYBOARD_IME,
        "com.example.voice/.VoiceIme",
        "com.example.keyboard/.MainIme",
    ]

    assert choose_manual_ime(enabled) == "com.example.keyboard/.MainIme"
    assert choose_manual_ime(
        enabled,
        preferred_ime="com.example.voice/.VoiceIme",
    ) == "com.example.voice/.VoiceIme"


def test_restore_system_keyboard_switches_and_verifies(monkeypatch) -> None:
    service = MobileKeyboardService()
    current = {"ime": ADB_KEYBOARD_IME}
    commands: list[list[str]] = []

    def fake_run(_device_id: str, args: list[str]) -> str:
        commands.append(args)
        if args == ["settings", "get", "secure", "default_input_method"]:
            return current["ime"]
        if args == ["ime", "list", "-s"]:
            return "\n".join(
                [ADB_KEYBOARD_IME, "com.example.keyboard/.MainIme"]
            )
        if args[:2] == ["ime", "set"]:
            current["ime"] = args[2]
            return f"Input method {args[2]} selected"
        raise AssertionError(args)

    monkeypatch.setattr(service, "_run", fake_run)
    result = service.restore_system_keyboard("device-1")

    assert result.changed is True
    assert result.restored_ime == "com.example.keyboard/.MainIme"
    assert ["ime", "set", "com.example.keyboard/.MainIme"] in commands


def test_restore_system_keyboard_is_noop_when_manual_ime_is_active(monkeypatch) -> None:
    service = MobileKeyboardService()
    commands: list[list[str]] = []

    def fake_run(_device_id: str, args: list[str]) -> str:
        commands.append(args)
        if args[0] == "settings":
            return "com.example.keyboard/.MainIme"
        return "com.example.keyboard/.MainIme\n" + ADB_KEYBOARD_IME

    monkeypatch.setattr(service, "_run", fake_run)
    result = service.restore_system_keyboard("device-1")

    assert result.changed is False
    assert result.restored_ime == "com.example.keyboard/.MainIme"
    assert not any(args[:2] == ["ime", "set"] for args in commands)


def test_restore_system_keyboard_reports_missing_manual_ime(monkeypatch) -> None:
    service = MobileKeyboardService()

    def fake_run(_device_id: str, args: list[str]) -> str:
        if args[0] == "settings":
            return ADB_KEYBOARD_IME
        return ADB_KEYBOARD_IME

    monkeypatch.setattr(service, "_run", fake_run)

    with pytest.raises(KeyboardRestoreError, match="没有已启用的系统输入法"):
        service.restore_system_keyboard("device-1")
