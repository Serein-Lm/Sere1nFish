"""Tool definitions for Gemini Agent function calling.

Maps device operations to OpenAI-compatible tool schemas.
Coordinates use 0-1000 relative scale (same as GLM agent).
"""

_COORDINATE = {"type": "integer", "minimum": 0, "maximum": 1000}
_DURATION_MS = {"type": "integer", "minimum": 100, "maximum": 5000}
_KEY_SCHEMA = {
    "type": "string",
    "enum": [
        "enter",
        "search",
        "delete",
        "tab",
        "menu",
        "escape",
        "space",
        "dpad_center",
        "dpad_up",
        "dpad_down",
        "dpad_left",
        "dpad_right",
        "app_switch",
    ],
}
_BATCH_LABEL = {
    "type": "string",
    "description": "Optional human-readable reason for this step.",
}
_BATCH_ACTION_SCHEMAS = [
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["tap"]},
            "x": _COORDINATE,
            "y": _COORDINATE,
            "label": _BATCH_LABEL,
        },
        "required": ["type", "x", "y"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["double_tap"]},
            "x": _COORDINATE,
            "y": _COORDINATE,
            "label": _BATCH_LABEL,
        },
        "required": ["type", "x", "y"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["long_press"]},
            "x": _COORDINATE,
            "y": _COORDINATE,
            "duration_ms": _DURATION_MS,
            "label": _BATCH_LABEL,
        },
        "required": ["type", "x", "y"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["swipe"]},
            "start_x": _COORDINATE,
            "start_y": _COORDINATE,
            "end_x": _COORDINATE,
            "end_y": _COORDINATE,
            "duration_ms": _DURATION_MS,
            "label": _BATCH_LABEL,
        },
        "required": ["type", "start_x", "start_y", "end_x", "end_y"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["type_text"]},
            "text": {"type": "string"},
            "label": _BATCH_LABEL,
        },
        "required": ["type", "text"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["launch_app"]},
            "app_name": {"type": "string"},
            "label": _BATCH_LABEL,
        },
        "required": ["type", "app_name"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["press_key"]},
            "key": _KEY_SCHEMA,
            "label": _BATCH_LABEL,
        },
        "required": ["type", "key"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["wait"]},
            "duration": {"type": "string"},
            "label": _BATCH_LABEL,
        },
        "required": ["type", "duration"],
        "additionalProperties": False,
    },
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["back", "home"]},
            "label": _BATCH_LABEL,
        },
        "required": ["type"],
        "additionalProperties": False,
    },
]

DEVICE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tap",
            "description": "Tap a visible target on the current screen. Coordinates are relative (0-1000 scale). Do not guess coordinates for elements that are not visible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {
                        "type": "integer",
                        "description": "X coordinate (0-1000)",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y coordinate (0-1000)",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "double_tap",
            "description": "Double tap a visible target on the current screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {
                        "type": "integer",
                        "description": "X coordinate (0-1000)",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y coordinate (0-1000)",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "long_press",
            "description": "Long press a visible target on the current screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {
                        "type": "integer",
                        "description": "X coordinate (0-1000)",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y coordinate (0-1000)",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "duration_ms": {
                        "type": "integer",
                        "description": "Press duration in milliseconds. Use 800-1500 for normal long press.",
                        "minimum": 300,
                        "maximum": 5000,
                    },
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "swipe",
            "description": "Swipe from one visible point to another. Use for scrolling, pulling to refresh, or moving sliders.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_x": {
                        "type": "integer",
                        "description": "Start X (0-1000)",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "start_y": {
                        "type": "integer",
                        "description": "Start Y (0-1000)",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "end_x": {
                        "type": "integer",
                        "description": "End X (0-1000)",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "end_y": {
                        "type": "integer",
                        "description": "End Y (0-1000)",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "duration_ms": {
                        "type": "integer",
                        "description": "Swipe duration in milliseconds. Use about 300-600 for scrolls.",
                        "minimum": 100,
                        "maximum": 5000,
                    },
                },
                "required": ["start_x", "start_y", "end_x", "end_y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into the currently focused input field. Tap the input field first if it is not focused.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_app",
            "description": "Launch an app by display name. Use this immediately for explicit open/start app requests.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "App name"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "back",
            "description": "Press the Android Back button once.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "home",
            "description": "Press the Android Home button once.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Press a supported Android key. Use enter/search after typing, dpad keys for focused controls, and app_switch for recent apps. Prefer the dedicated back/home tools for navigation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "enum": [
                            "enter",
                            "search",
                            "delete",
                            "tab",
                            "menu",
                            "escape",
                            "space",
                            "dpad_center",
                            "dpad_up",
                            "dpad_down",
                            "dpad_left",
                            "dpad_right",
                            "app_switch",
                        ],
                        "description": "Supported key event.",
                    },
                },
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait briefly for loading, animations, or network content before the next action.",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration": {
                        "type": "string",
                        "description": "Duration such as '0.5 seconds', '1 second', or '1500 ms'. Prefer short waits.",
                    },
                },
                "required": ["duration"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "batch_actions",
            "description": (
                "Execute a short deterministic sequence of phone actions in one "
                "tool call. Use this to reduce latency when the next actions do "
                "not require a fresh screenshot between them, such as home + "
                "launch_app + wait, or tapping a visible input + type_text + "
                "press_key search. The executor runs actions sequentially with "
                "per-step and total timeouts, stops on failure by default, and "
                "returns per-step feedback."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "description": "Ordered actions to execute. Keep batches short and deterministic.",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {
                            "oneOf": _BATCH_ACTION_SCHEMAS,
                        },
                    },
                    "stop_on_error": {
                        "type": "boolean",
                        "description": "Stop after the first failed action. Default true.",
                    },
                    "step_timeout_ms": {
                        "type": "integer",
                        "description": "Maximum time for one action. Default 5000, capped at 10000.",
                        "minimum": 500,
                        "maximum": 10000,
                    },
                    "total_timeout_ms": {
                        "type": "integer",
                        "description": "Maximum total batch time. Default 20000, capped at 30000.",
                        "minimum": 1000,
                        "maximum": 30000,
                    },
                },
                "required": ["actions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_and_search",
            "description": (
                "Launch an app and search in one tool call: launch_app, wait for "
                "load, optionally tap the search entry to reveal the input, type "
                "the query, then submit. Use this for the common 'open <app> and "
                "search <keyword>' flow. Only use it when the app's search entry "
                "location is known/stable (pass search_entry_x/y); if you are "
                "unsure where the search entry is, call launch_app first and "
                "observe a screenshot instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "App display name to launch."},
                    "query": {"type": "string", "description": "The search keyword/phrase to type."},
                    "search_entry_x": {
                        "type": "integer",
                        "description": "X (0-1000) of the search icon/entry to tap after launch. Omit if the app opens to a focused search field.",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "search_entry_y": {
                        "type": "integer",
                        "description": "Y (0-1000) of the search icon/entry to tap after launch.",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "submit_key": {
                        "type": "string",
                        "enum": ["enter", "search"],
                        "description": "Key used to submit the search. Default 'search'.",
                    },
                    "app_load_ms": {
                        "type": "integer",
                        "description": "Wait after launch for the app to load. Default 1500.",
                        "minimum": 0,
                        "maximum": 8000,
                    },
                    "focus_wait_ms": {
                        "type": "integer",
                        "description": "Wait after tapping the search entry before typing. Default 500.",
                        "minimum": 0,
                        "maximum": 5000,
                    },
                },
                "required": ["app_name", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Perform a search in one tool call: optionally tap the search "
                "box, type the query, then submit. Use this whenever the goal is "
                "to search for something (articles, users, products) and the "
                "search entry is visible or already focused. This replaces the "
                "repeated tap-input + type_text + press_key sequence, so you do "
                "not need a separate LLM step per micro-action. If the search box "
                "is visible, pass its coordinates as input_x/input_y; if the "
                "field is already focused, omit them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search keyword/phrase to type.",
                    },
                    "input_x": {
                        "type": "integer",
                        "description": "X (0-1000) of the search box to tap first. Omit if already focused.",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "input_y": {
                        "type": "integer",
                        "description": "Y (0-1000) of the search box to tap first. Omit if already focused.",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                    "submit_key": {
                        "type": "string",
                        "enum": ["enter", "search"],
                        "description": "Key used to submit the search. Default 'search'.",
                    },
                    "pre_wait_ms": {
                        "type": "integer",
                        "description": "Wait after tapping the box before typing (keyboard/focus settle). Default 400.",
                        "minimum": 0,
                        "maximum": 5000,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Finish the task. Use success summary when completed, or a clear reason such as ELEMENT_NOT_FOUND when blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Summary of result"},
                },
                "required": ["message"],
            },
        },
    },
]
