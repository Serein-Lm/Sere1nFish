"""Tests for Gemini Agent components."""

import asyncio

from AutoGLM_GUI.agents.gemini.action_mapper import tool_call_to_action
from AutoGLM_GUI.agents.gemini.tools import DEVICE_TOOLS
from AutoGLM_GUI.config import AgentConfig, ModelConfig
from AutoGLM_GUI.device_protocol import Screenshot


class TestDeviceTools:
    def test_tool_count(self):
        assert len(DEVICE_TOOLS) == 14

    def test_all_tools_have_required_fields(self):
        for tool in DEVICE_TOOLS:
            assert tool["type"] == "function"
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

    def test_tool_names(self):
        names = {t["function"]["name"] for t in DEVICE_TOOLS}
        expected = {
            "tap",
            "double_tap",
            "long_press",
            "swipe",
            "type_text",
            "launch_app",
            "back",
            "home",
            "press_key",
            "wait",
            "batch_actions",
            "open_and_search",
            "search",
            "finish",
        }
        assert names == expected


class TestActionMapper:
    def test_tap(self):
        result = tool_call_to_action("tap", {"x": 500, "y": 300})
        assert result == {"_metadata": "do", "action": "Tap", "element": [500, 300]}

    def test_tap_accepts_coordinate_alias(self):
        result = tool_call_to_action("tap", {"coordinate": [500, 300]})
        assert result == {"_metadata": "do", "action": "Tap", "element": [500, 300]}

    def test_tap_accepts_point_object_alias(self):
        result = tool_call_to_action("tap", {"point": {"x": 120, "y": 240}})
        assert result == {"_metadata": "do", "action": "Tap", "element": [120, 240]}

    def test_tap_accepts_coordinate_pair_in_x(self):
        result = tool_call_to_action(
            "tap", {"x": [308, 275], "y": [308, 275]}
        )
        assert result == {"_metadata": "do", "action": "Tap", "element": [308, 275]}

    def test_double_tap(self):
        result = tool_call_to_action("double_tap", {"x": 100, "y": 200})
        assert result == {
            "_metadata": "do",
            "action": "Double Tap",
            "element": [100, 200],
        }

    def test_long_press(self):
        result = tool_call_to_action("long_press", {"x": 750, "y": 800})
        assert result == {
            "_metadata": "do",
            "action": "Long Press",
            "element": [750, 800],
        }

    def test_swipe(self):
        result = tool_call_to_action(
            "swipe",
            {
                "start_x": 500,
                "start_y": 700,
                "end_x": 500,
                "end_y": 300,
            },
        )
        assert result == {
            "_metadata": "do",
            "action": "Swipe",
            "start": [500, 700],
            "end": [500, 300],
        }

    def test_swipe_accepts_pair_aliases(self):
        result = tool_call_to_action(
            "swipe",
            {
                "start": [500, 700],
                "end": [500, 300],
            },
        )
        assert result == {
            "_metadata": "do",
            "action": "Swipe",
            "start": [500, 700],
            "end": [500, 300],
        }

    def test_type_text(self):
        result = tool_call_to_action("type_text", {"text": "Hello"})
        assert result == {"_metadata": "do", "action": "Type", "text": "Hello"}

    def test_launch_app(self):
        result = tool_call_to_action("launch_app", {"app_name": "WeChat"})
        assert result == {"_metadata": "do", "action": "Launch", "app": "WeChat"}

    def test_back(self):
        result = tool_call_to_action("back", {})
        assert result == {"_metadata": "do", "action": "Back"}

    def test_home(self):
        result = tool_call_to_action("home", {})
        assert result == {"_metadata": "do", "action": "Home"}

    def test_wait(self):
        result = tool_call_to_action("wait", {"duration": "2 seconds"})
        assert result == {"_metadata": "do", "action": "Wait", "duration": "2 seconds"}

    def test_finish(self):
        result = tool_call_to_action("finish", {"message": "Done"})
        assert result == {"_metadata": "finish", "message": "Done"}

    def test_unknown_tool(self):
        result = tool_call_to_action("unknown_tool", {})
        assert result["_metadata"] == "finish"
        assert "Unknown tool" in result["message"]

    def test_missing_args_returns_retryable_invalid_action(self):
        """Missing required args should be reported without ending the subtask."""
        result = tool_call_to_action("tap", {})
        assert result["_metadata"] == "do"
        assert result["action"] == "Invalid Tool Call"
        assert result["_invalid_tool_call"] is True
        assert "Invalid tool call" in result["message"]

    def test_invalid_arg_type_returns_retryable_invalid_action(self):
        """Non-numeric coordinate should be reported without ending the subtask."""
        result = tool_call_to_action("tap", {"x": "click_here", "y": 100})
        assert result["_metadata"] == "do"
        assert result["action"] == "Invalid Tool Call"
        assert result["_invalid_tool_call"] is True
        assert "Invalid tool call" in result["message"]

    def test_float_coords_converted_to_int(self):
        """Float coordinates from LLM should be accepted and converted."""
        result = tool_call_to_action("tap", {"x": 500.5, "y": 300.7})
        assert result == {"_metadata": "do", "action": "Tap", "element": [500, 300]}

    def test_batch_actions(self):
        result = tool_call_to_action(
            "batch_actions",
            {
                "actions": [
                    {"type": "home", "label": "go home"},
                    {"type": "launch_app", "app_name": "应用商店"},
                    {"type": "tap", "coordinate": [500, 300]},
                    {"type": "wait", "duration": "0.5 seconds"},
                ],
                "step_timeout_ms": 3000,
                "total_timeout_ms": 9000,
            },
        )

        assert result["_metadata"] == "batch"
        assert result["stop_on_error"] is True
        assert result["step_timeout_ms"] == 3000
        assert result["total_timeout_ms"] == 9000
        assert result["actions"] == [
            {"_metadata": "do", "action": "Home", "label": "go home"},
            {"_metadata": "do", "action": "Launch", "app": "应用商店"},
            {"_metadata": "do", "action": "Tap", "element": [500, 300]},
            {"_metadata": "do", "action": "Wait", "duration": "0.5 seconds"},
        ]

    def test_batch_actions_clamps_timeouts(self):
        result = tool_call_to_action(
            "batch_actions",
            {
                "actions": [{"type": "home"}],
                "step_timeout_ms": 99,
                "total_timeout_ms": 999999,
            },
        )

        assert result["step_timeout_ms"] == 500
        assert result["total_timeout_ms"] == 30000

    def test_batch_schema_requires_tap_coordinates(self):
        batch_tool = next(
            tool for tool in DEVICE_TOOLS if tool["function"]["name"] == "batch_actions"
        )
        action_schemas = batch_tool["function"]["parameters"]["properties"]["actions"][
            "items"
        ]["oneOf"]
        tap_schema = next(
            schema
            for schema in action_schemas
            if schema["properties"]["type"]["enum"] == ["tap"]
        )

        assert tap_schema["required"] == ["type", "x", "y"]


class TestAgentRegistration:
    def test_gemini_registered(self):
        from AutoGLM_GUI.agents import is_agent_type_registered

        assert is_agent_type_registered("gemini")
        assert is_agent_type_registered("general-vision")

    def test_gemini_in_list(self):
        from AutoGLM_GUI.agents import list_agent_types

        types = list_agent_types()
        assert "gemini" in types
        assert "general-vision" in types


class TestEventTypes:
    def test_event_enum_matches_actual_events(self):
        """AgentEventType values must match the strings agents actually emit."""
        from AutoGLM_GUI.agents.events import AgentEventType

        assert AgentEventType.THINKING == "thinking"
        assert AgentEventType.STEP == "step"
        assert AgentEventType.DONE == "done"
        assert AgentEventType.ERROR == "error"
        assert AgentEventType.CANCELLED == "cancelled"


class TestCoordinateClamping:
    def test_clamp_negative_coordinates(self):
        from AutoGLM_GUI.actions.handler import ActionHandler

        handler = ActionHandler.__new__(ActionHandler)
        x, y = handler._convert_relative_to_absolute([-100, -50], 1080, 1920)
        assert x == 0
        assert y == 0

    def test_clamp_overflow_coordinates(self):
        from AutoGLM_GUI.actions.handler import ActionHandler

        handler = ActionHandler.__new__(ActionHandler)
        x, y = handler._convert_relative_to_absolute([1500, 2000], 1080, 1920)
        assert x == 1080
        assert y == 1920

    def test_normal_coordinates_unchanged(self):
        from AutoGLM_GUI.actions.handler import ActionHandler

        handler = ActionHandler.__new__(ActionHandler)
        x, y = handler._convert_relative_to_absolute([500, 500], 1080, 1920)
        assert x == 540
        assert y == 960

    def test_invalid_tool_call_does_not_finish_subtask(self):
        from AutoGLM_GUI.actions.handler import ActionHandler

        handler = ActionHandler.__new__(ActionHandler)
        result = handler.execute(
            {
                "_metadata": "do",
                "action": "Invalid Tool Call",
                "message": "Invalid tool call: Missing required argument: 'x'",
            },
            1000,
            1000,
        )

        assert result.success is False
        assert result.should_finish is False
        assert "Missing required argument" in result.message


class _BatchDummyDevice:
    device_id = "dummy-device"

    def __init__(self):
        self.calls = []

    def get_screenshot(self, timeout: int = 10):
        return Screenshot(base64_data="shot", width=1000, height=2000)

    def get_current_app(self):
        return "Home"

    def tap(self, x: int, y: int, delay: float | None = None) -> None:
        self.calls.append(("tap", x, y))

    def double_tap(self, x: int, y: int, delay: float | None = None) -> None:
        self.calls.append(("double_tap", x, y))

    def long_press(
        self, x: int, y: int, duration_ms: int = 3000, delay: float | None = None
    ) -> None:
        self.calls.append(("long_press", x, y, duration_ms))

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int | None = None,
        delay: float | None = None,
    ) -> None:
        self.calls.append(("swipe", start_x, start_y, end_x, end_y, duration_ms))

    def type_text(self, text: str) -> None:
        self.calls.append(("type_text", text))

    def clear_text(self) -> None:
        self.calls.append(("clear_text",))

    def back(self, delay: float | None = None) -> None:
        self.calls.append(("back",))

    def home(self, delay: float | None = None) -> None:
        self.calls.append(("home",))

    def press_key(self, key: str, delay: float | None = None) -> bool:
        self.calls.append(("press_key", key))
        return True

    def launch_app(self, app_name: str, delay: float | None = None) -> bool:
        self.calls.append(("launch_app", app_name))
        return True

    def detect_and_set_adb_keyboard(self) -> str:
        return "com.android.adbkeyboard/.AdbIME"

    def restore_keyboard(self, ime: str) -> None:
        self.calls.append(("restore_keyboard", ime))


class TestBatchActionExecution:
    def test_batch_action_executes_steps_and_returns_feedback(self):
        from AutoGLM_GUI.agents.gemini.async_agent import AsyncGeminiAgent

        async def run():
            device = _BatchDummyDevice()
            agent = AsyncGeminiAgent(
                model_config=ModelConfig(
                    base_url="http://example.test/v1",
                    api_key="test",
                    model_name="test-model",
                ),
                agent_config=AgentConfig(device_id="dummy-device"),
                device=device,
            )
            action = tool_call_to_action(
                "batch_actions",
                {
                    "actions": [
                        {"type": "home"},
                        {"type": "tap", "x": 500, "y": 500},
                        {"type": "type_text", "text": "微信"},
                        {"type": "press_key", "key": "search"},
                    ],
                    "step_timeout_ms": 5000,
                    "total_timeout_ms": 10000,
                },
            )

            result, screenshot, feedback = await agent._execute_batch_action(action)
            return device, result, screenshot, feedback

        device, result, screenshot, feedback = asyncio.run(run())

        assert result.success is True
        assert screenshot.base64_data == "shot"
        assert [item["success"] for item in feedback] == [True, True, True, True]
        assert device.calls == [
            ("home",),
            ("tap", 500, 1000),
            ("clear_text",),
            ("type_text", "微信"),
            ("press_key", "search"),
        ]

    def test_execute_step_returns_batch_feedback(self):
        from AutoGLM_GUI.agents.gemini.async_agent import AsyncGeminiAgent

        async def run():
            device = _BatchDummyDevice()
            agent = AsyncGeminiAgent(
                model_config=ModelConfig(
                    base_url="http://example.test/v1",
                    api_key="test",
                    model_name="test-model",
                ),
                agent_config=AgentConfig(device_id="dummy-device"),
                device=device,
            )

            async def fake_call_llm_with_tools():
                return (
                    "",
                    "batch_actions",
                    {
                        "actions": [
                            {"type": "home"},
                            {"type": "press_key", "key": "search"},
                        ],
                    },
                )

            agent._call_llm_with_tools = fake_call_llm_with_tools

            events = []
            async for event in agent._execute_step():
                events.append(event)
            return device, events

        device, events = asyncio.run(run())
        step = events[-1]

        assert step["type"] == "step"
        assert step["data"]["success"] is True
        assert step["data"]["batch_results"] is not None
        assert [item["success"] for item in step["data"]["batch_results"]] == [
            True,
            True,
        ]
        assert device.calls == [("home",), ("press_key", "search")]
