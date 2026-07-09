from collections.abc import Callable
import re
from typing import Any

from AutoGLM_GUI.adb.timing import TIMING_CONFIG
from AutoGLM_GUI.device_protocol import DeviceProtocol
from AutoGLM_GUI.trace import trace_sleep, trace_span

from .types import ActionResult


class ActionHandler:
    def __init__(
        self,
        device: DeviceProtocol,
        confirmation_callback: Callable[[str], bool] | None = None,
        takeover_callback: Callable[[str], None] | None = None,
    ):
        self.device = device
        self.confirmation_callback = confirmation_callback or self._default_confirmation
        self.takeover_callback = takeover_callback or self._default_takeover

    def execute(
        self, action: dict[str, Any], screen_width: int, screen_height: int
    ) -> ActionResult:
        action_type = action.get("_metadata")
        action_name = action.get("action")
        with trace_span(
            "action.execute",
            attrs={
                "action_type": action_type,
                "action_name": action_name,
                "screen_width": screen_width,
                "screen_height": screen_height,
            },
        ) as span:
            if action_type == "finish":
                message = str(action.get("message") or "")
                success = not action.get("_invalid_tool_call") and self._is_success_finish(
                    message
                )
                result = ActionResult(
                    success=success, should_finish=True, message=message
                )
                span.set_attributes(
                    {
                        "success": result.success,
                        "should_finish": result.should_finish,
                    }
                )
                return result

            if action_type != "do":
                result = ActionResult(
                    success=False,
                    should_finish=True,
                    message=f"Unknown action type: {action_type}",
                )
                span.set_attributes(
                    {
                        "success": result.success,
                        "should_finish": result.should_finish,
                    }
                )
                return result

            if not isinstance(action_name, str) or not action_name:
                result = ActionResult(
                    success=False,
                    should_finish=False,
                    message=f"Unknown action: {action_name}",
                )
                span.set_attributes(
                    {
                        "success": result.success,
                        "should_finish": result.should_finish,
                    }
                )
                return result

            handler_method = self._get_handler(action_name)

            if handler_method is None:
                result = ActionResult(
                    success=False,
                    should_finish=False,
                    message=f"Unknown action: {action_name}",
                )
                span.set_attributes(
                    {
                        "success": result.success,
                        "should_finish": result.should_finish,
                    }
                )
                return result

            try:
                result = handler_method(action, screen_width, screen_height)
                span.set_attributes(
                    {
                        "success": result.success,
                        "should_finish": result.should_finish,
                    }
                )
                return result
            except Exception as e:
                result = ActionResult(
                    success=False, should_finish=False, message=f"Action failed: {e}"
                )
                span.set_attributes(
                    {
                        "success": result.success,
                        "should_finish": result.should_finish,
                    }
                )
                return result

    def _get_handler(
        self, action_name: str
    ) -> Callable[[dict[str, Any], int, int], ActionResult] | None:
        handlers = {
            "Launch": self._handle_launch,
            "Tap": self._handle_tap,
            "Type": self._handle_type,
            "Type_Name": self._handle_type,
            "Swipe": self._handle_swipe,
            "Back": self._handle_back,
            "Home": self._handle_home,
            "Press Key": self._handle_press_key,
            "Double Tap": self._handle_double_tap,
            "Long Press": self._handle_long_press,
            "Wait": self._handle_wait,
            "Take_over": self._handle_takeover,
            "Note": self._handle_note,
            "Invalid Tool Call": self._handle_invalid_tool_call,
        }
        return handlers.get(action_name)

    def _convert_relative_to_absolute(
        self, element: list[int], screen_width: int, screen_height: int
    ) -> tuple[int, int]:
        clamped_x = max(0, min(element[0], 1000))
        clamped_y = max(0, min(element[1], 1000))
        x = int(clamped_x / 1000 * screen_width)
        y = int(clamped_y / 1000 * screen_height)
        return x, y

    @staticmethod
    def _is_success_finish(message: str) -> bool:
        text = message.strip()
        upper = text.upper()
        failure_markers = (
            "ELEMENT_NOT_FOUND",
            "STEP_LIMIT_EXCEEDED",
            "INVALID",
            "ERROR",
            "FAILED",
            "FAIL",
        )
        if any(marker in upper for marker in failure_markers):
            return False
        return not any(
            marker in text
            for marker in ("无法", "未找到", "找不到", "失败", "错误", "不能完成")
        )

    def _handle_launch(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        app_name = action.get("app")
        if not app_name:
            return ActionResult(False, False, "No app name specified")

        success = self.device.launch_app(app_name, delay=0.2)
        if success:
            return ActionResult(True, False)
        return ActionResult(False, False, f"App not found: {app_name}")

    def _handle_tap(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        element = action.get("element")
        if not element:
            return ActionResult(False, False, "No element coordinates")

        x, y = self._convert_relative_to_absolute(element, width, height)

        if "message" in action:
            if not self.confirmation_callback(action["message"]):
                return ActionResult(
                    success=False,
                    should_finish=True,
                    message="User cancelled sensitive operation",
                )

        self.device.tap(x, y, delay=0.1)
        return ActionResult(True, False)

    _ADB_IME = "com.android.adbkeyboard/.AdbIME"

    def _handle_type(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        text = action.get("text", "")

        original_ime = self.device.detect_and_set_adb_keyboard()
        need_restore = self._ADB_IME not in original_ime

        if need_restore:
            trace_sleep(
                TIMING_CONFIG.action.keyboard_switch_delay,
                name="sleep.keyboard_switch",
                attrs={"action_name": "Type"},
            )

        self.device.clear_text()
        trace_sleep(
            TIMING_CONFIG.action.text_clear_delay,
            name="sleep.text_clear_delay",
            attrs={"action_name": "Type"},
        )

        self.device.type_text(text)
        trace_sleep(
            TIMING_CONFIG.action.text_input_delay,
            name="sleep.text_input_delay",
            attrs={"action_name": "Type", "text_length": len(text)},
        )

        if need_restore:
            self.device.restore_keyboard(original_ime)
            trace_sleep(
                TIMING_CONFIG.action.keyboard_restore_delay,
                name="sleep.keyboard_restore_delay",
                attrs={"action_name": "Type"},
            )

        return ActionResult(True, False)

    def _handle_swipe(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        start = action.get("start")
        end = action.get("end")

        if not start or not end:
            return ActionResult(False, False, "Missing swipe coordinates")

        start_x, start_y = self._convert_relative_to_absolute(start, width, height)
        end_x, end_y = self._convert_relative_to_absolute(end, width, height)

        duration_ms = int(action.get("duration_ms") or 450)
        self.device.swipe(start_x, start_y, end_x, end_y, duration_ms, delay=0.1)
        return ActionResult(True, False)

    def _handle_back(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        self.device.back(delay=0.1)
        return ActionResult(True, False)

    def _handle_home(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        self.device.home(delay=0.1)
        return ActionResult(True, False)

    def _handle_press_key(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        key = str(action.get("key") or "").strip().lower()
        if not key:
            return ActionResult(False, False, "No key specified")
        ok = self.device.press_key(key, delay=0.1)
        if ok:
            return ActionResult(True, False)
        return ActionResult(False, False, f"Unsupported key: {key}")

    def _handle_double_tap(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        element = action.get("element")
        if not element:
            return ActionResult(False, False, "No element coordinates")

        x, y = self._convert_relative_to_absolute(element, width, height)
        self.device.double_tap(x, y, delay=0.1)
        return ActionResult(True, False)

    def _handle_long_press(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        element = action.get("element")
        if not element:
            return ActionResult(False, False, "No element coordinates")

        x, y = self._convert_relative_to_absolute(element, width, height)
        duration_ms = int(action.get("duration_ms") or 1200)
        self.device.long_press(x, y, duration_ms=duration_ms, delay=0.1)
        return ActionResult(True, False)

    MAX_WAIT_SECONDS = 30
    MIN_WAIT_SECONDS = 0.1

    def _parse_wait_duration(self, value: Any) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if not isinstance(value, str):
            return 1.0
        text = value.strip().lower()
        if not text:
            return 1.0
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if not match:
            return 1.0
        duration = float(match.group(1))
        if "ms" in text or "毫秒" in text:
            duration /= 1000.0
        return duration

    def _handle_wait(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        duration = self._parse_wait_duration(action.get("duration", "1 seconds"))
        duration = max(self.MIN_WAIT_SECONDS, min(duration, self.MAX_WAIT_SECONDS))
        trace_sleep(
            duration,
            name="sleep.wait_action",
            attrs={"action_name": "Wait"},
        )
        return ActionResult(True, False)

    def _handle_takeover(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        message = action.get("message", "User intervention required")
        self.takeover_callback(message)
        return ActionResult(True, False)

    def _handle_note(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        return ActionResult(True, False)

    def _handle_invalid_tool_call(
        self, action: dict[str, Any], width: int, height: int
    ) -> ActionResult:
        return ActionResult(
            success=False,
            should_finish=False,
            message=str(action.get("message") or "Invalid tool call"),
        )

    @staticmethod
    def _default_confirmation(message: str) -> bool:
        response = input(f"\n⚠️  Confirm action: {message} (y/n): ")
        return response.lower() in ("y", "yes")

    @staticmethod
    def _default_takeover(message: str) -> None:
        input(f"\n🤚 {message}. Press Enter to continue...")
