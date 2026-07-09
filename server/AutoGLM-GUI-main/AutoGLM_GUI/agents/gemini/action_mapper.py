"""Tool-call to action mapping for Gemini Agent."""

from typing import Any

from AutoGLM_GUI.logger import logger


def _require_int(args: dict[str, Any], key: str) -> int:
    """Extract and validate an integer argument from tool call args."""
    val = args.get(key)
    if val is None:
        raise ValueError(f"Missing required argument: '{key}'")
    if not isinstance(val, (int, float)):
        raise ValueError(
            f"Expected number for '{key}', got {type(val).__name__}: {val!r}"
        )
    return int(val)


def _coerce_int(value: Any, *, key: str) -> int:
    if not isinstance(value, (int, float)):
        raise ValueError(
            f"Expected number for '{key}', got {type(value).__name__}: {value!r}"
        )
    return int(value)


def _coordinate_pair(value: Any, *, key: str) -> list[int]:
    """Accept common coordinate encodings from non-strict tool callers."""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return [
            _coerce_int(value[0], key=f"{key}[0]"),
            _coerce_int(value[1], key=f"{key}[1]"),
        ]
    if isinstance(value, dict):
        return [
            _coerce_int(value.get("x"), key=f"{key}.x"),
            _coerce_int(value.get("y"), key=f"{key}.y"),
        ]
    raise ValueError(
        f"Expected coordinate pair for '{key}', got {type(value).__name__}: {value!r}"
    )


def _require_xy(
    args: dict[str, Any],
    *,
    aliases: tuple[str, ...] = ("coordinate", "coordinates", "point", "element"),
) -> list[int]:
    """Return an [x, y] pair, accepting x/y or common coordinate aliases."""
    if args.get("x") is not None or args.get("y") is not None:
        return [_require_int(args, "x"), _require_int(args, "y")]
    for alias in aliases:
        if args.get(alias) is not None:
            return _coordinate_pair(args[alias], key=alias)
    raise ValueError("Missing required argument: 'x'")


def _require_pair_alias(
    args: dict[str, Any],
    *,
    x_key: str,
    y_key: str,
    aliases: tuple[str, ...],
) -> list[int]:
    if args.get(x_key) is not None or args.get(y_key) is not None:
        return [_require_int(args, x_key), _require_int(args, y_key)]
    for alias in aliases:
        if args.get(alias) is not None:
            return _coordinate_pair(args[alias], key=alias)
    raise ValueError(f"Missing required argument: '{x_key}'")


def _require_str(args: dict[str, Any], key: str) -> str:
    """Extract and validate a string argument from tool call args."""
    val = args.get(key)
    if val is None:
        raise ValueError(f"Missing required argument: '{key}'")
    if not isinstance(val, str):
        raise ValueError(
            f"Expected string for '{key}', got {type(val).__name__}: {val!r}"
        )
    return val


def _optional_int(args: dict[str, Any], key: str) -> int | None:
    if args.get(key) is None:
        return None
    return _require_int(args, key)


def _optional_bool(args: dict[str, Any], key: str, default: bool) -> bool:
    val = args.get(key)
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    raise ValueError(f"Expected boolean for '{key}', got {type(val).__name__}: {val!r}")


def _clamped_int(value: int | None, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        value = default
    return max(minimum, min(int(value), maximum))


def tool_call_to_action(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Convert a function call to an ActionHandler-compatible action dict.

    Args:
        tool_name: The function name called by the model.
        arguments: The parsed arguments dict.

    Returns:
        Action dict compatible with ActionHandler.execute().
    """
    if tool_name == "finish":
        return {
            "_metadata": "finish",
            "message": arguments.get("message", "Task completed"),
        }

    try:
        return _build_action(tool_name, arguments)
    except (ValueError, KeyError) as e:
        logger.warning(f"Invalid tool arguments for {tool_name}: {e}")
        return {
            "_metadata": "do",
            "action": "Invalid Tool Call",
            "_invalid_tool_call": True,
            "message": f"Invalid tool call: {e}",
        }


def _build_action(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Build action dict with validated arguments."""
    if tool_name == "batch_actions":
        raw_actions = args.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            raise ValueError("batch_actions requires a non-empty actions list")
        if len(raw_actions) > 8:
            raise ValueError("batch_actions supports at most 8 actions")

        actions: list[dict[str, Any]] = []
        for index, item in enumerate(raw_actions):
            if not isinstance(item, dict):
                raise ValueError(f"actions[{index}] must be an object")
            action_type = _require_str(item, "type")
            built = _build_action(action_type, item)
            if built.get("_metadata") != "do":
                raise ValueError(f"actions[{index}] has unsupported type: {action_type}")
            if item.get("label") is not None:
                built["label"] = _require_str(item, "label")
            actions.append(built)

        return {
            "_metadata": "batch",
            "actions": actions,
            "stop_on_error": _optional_bool(args, "stop_on_error", True),
            "step_timeout_ms": _clamped_int(
                _optional_int(args, "step_timeout_ms"), 5000, 500, 10000
            ),
            "total_timeout_ms": _clamped_int(
                _optional_int(args, "total_timeout_ms"), 20000, 1000, 30000
            ),
        }

    if tool_name == "open_and_search":
        app_name = _require_str(args, "app_name")
        query = _require_str(args, "query")
        submit_key = args.get("submit_key") or "search"
        if submit_key not in ("enter", "search"):
            raise ValueError(f"Unsupported submit_key: {submit_key!r}")
        app_load_ms = _clamped_int(_optional_int(args, "app_load_ms"), 1500, 0, 8000)
        focus_wait_ms = _clamped_int(_optional_int(args, "focus_wait_ms"), 500, 0, 5000)

        sub_actions: list[dict[str, Any]] = [
            {"_metadata": "do", "action": "Launch", "app": app_name, "label": "launch app"},
        ]
        if app_load_ms > 0:
            sub_actions.append(
                {"_metadata": "do", "action": "Wait", "duration": f"{app_load_ms} ms", "label": "wait app load"}
            )
        if args.get("search_entry_x") is not None or args.get("search_entry_y") is not None:
            sub_actions.append(
                {
                    "_metadata": "do",
                    "action": "Tap",
                    "element": [_require_int(args, "search_entry_x"), _require_int(args, "search_entry_y")],
                    "label": "tap search entry",
                }
            )
            if focus_wait_ms > 0:
                sub_actions.append(
                    {"_metadata": "do", "action": "Wait", "duration": f"{focus_wait_ms} ms", "label": "wait focus"}
                )
        sub_actions.append(
            {"_metadata": "do", "action": "Type", "text": query, "label": "type query"}
        )
        sub_actions.append(
            {"_metadata": "do", "action": "Press Key", "key": submit_key, "label": "submit search"}
        )

        return {
            "_metadata": "batch",
            "actions": sub_actions,
            "stop_on_error": True,
            "step_timeout_ms": 10000,
            "total_timeout_ms": 30000,
        }

    if tool_name == "search":
        query = _require_str(args, "query")
        submit_key = args.get("submit_key") or "search"
        if submit_key not in ("enter", "search"):
            raise ValueError(f"Unsupported submit_key: {submit_key!r}")
        pre_wait_ms = _clamped_int(_optional_int(args, "pre_wait_ms"), 400, 0, 5000)

        sub_actions: list[dict[str, Any]] = []
        has_x = args.get("input_x") is not None
        has_y = args.get("input_y") is not None
        if has_x or has_y:
            sub_actions.append(
                {
                    "_metadata": "do",
                    "action": "Tap",
                    "element": [_require_int(args, "input_x"), _require_int(args, "input_y")],
                    "label": "tap search box",
                }
            )
            if pre_wait_ms > 0:
                sub_actions.append(
                    {
                        "_metadata": "do",
                        "action": "Wait",
                        "duration": f"{pre_wait_ms} ms",
                        "label": "wait for keyboard/focus",
                    }
                )
        sub_actions.append(
            {"_metadata": "do", "action": "Type", "text": query, "label": "type query"}
        )
        sub_actions.append(
            {
                "_metadata": "do",
                "action": "Press Key",
                "key": submit_key,
                "label": "submit search",
            }
        )

        return {
            "_metadata": "batch",
            "actions": sub_actions,
            "stop_on_error": True,
            "step_timeout_ms": 8000,
            "total_timeout_ms": 20000,
        }

    if tool_name == "tap":
        return {
            "_metadata": "do",
            "action": "Tap",
            "element": _require_xy(args),
        }

    if tool_name == "double_tap":
        return {
            "_metadata": "do",
            "action": "Double Tap",
            "element": _require_xy(args),
        }

    if tool_name == "long_press":
        action = {
            "_metadata": "do",
            "action": "Long Press",
            "element": _require_xy(args),
        }
        if args.get("duration_ms") is not None:
            action["duration_ms"] = _require_int(args, "duration_ms")
        return action

    if tool_name == "swipe":
        start = _require_pair_alias(
            args,
            x_key="start_x",
            y_key="start_y",
            aliases=("start", "start_coordinate", "from", "coordinate"),
        )
        end = _require_pair_alias(
            args,
            x_key="end_x",
            y_key="end_y",
            aliases=("end", "end_coordinate", "to", "coordinate2"),
        )
        action = {
            "_metadata": "do",
            "action": "Swipe",
            "start": start,
            "end": end,
        }
        if args.get("duration_ms") is not None:
            action["duration_ms"] = _require_int(args, "duration_ms")
        return action

    if tool_name == "type_text":
        return {"_metadata": "do", "action": "Type", "text": _require_str(args, "text")}

    if tool_name == "launch_app":
        return {
            "_metadata": "do",
            "action": "Launch",
            "app": _require_str(args, "app_name"),
        }

    if tool_name == "back":
        return {"_metadata": "do", "action": "Back"}

    if tool_name == "home":
        return {"_metadata": "do", "action": "Home"}

    if tool_name == "press_key":
        return {"_metadata": "do", "action": "Press Key", "key": _require_str(args, "key")}

    if tool_name == "wait":
        return {
            "_metadata": "do",
            "action": "Wait",
            "duration": args.get("duration", "1 seconds"),
        }

    return {
        "_metadata": "finish",
        "_invalid_tool_call": True,
        "message": f"Unknown tool: {tool_name}",
    }
