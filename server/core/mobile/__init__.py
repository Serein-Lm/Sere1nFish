"""Core mobile capabilities shared across the platform.

The mobile package reuses the vendored AutoGLM-GUI sources.  Keep those sources
available for direct ``core.mobile`` imports, but load the heavier ADB/scrcpy
objects only when callers request them.  This keeps lightweight modules such as
``core.mobile.easytier`` importable without initializing the device stack.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, TYPE_CHECKING


_autoglm_src = Path(__file__).resolve().parents[2] / "AutoGLM-GUI-main"
if _autoglm_src.exists() and str(_autoglm_src) not in sys.path:
    sys.path.insert(0, str(_autoglm_src))


if TYPE_CHECKING:
    from core.mobile.device import DeviceCapabilityError, MobileDevice
    from core.mobile.manager import MobileDeviceManager
    from core.mobile.session import MobileSessionManager, SessionState
    from core.mobile.stream import MobileStreamSession


def __getattr__(name: str) -> Any:
    if name in {"MobileDevice", "DeviceCapabilityError"}:
        from core.mobile.device import DeviceCapabilityError, MobileDevice

        values = {
            "MobileDevice": MobileDevice,
            "DeviceCapabilityError": DeviceCapabilityError,
        }
    elif name == "MobileDeviceManager":
        from core.mobile.manager import MobileDeviceManager

        values = {"MobileDeviceManager": MobileDeviceManager}
    elif name in {"MobileSessionManager", "SessionState"}:
        from core.mobile.session import MobileSessionManager, SessionState

        values = {
            "MobileSessionManager": MobileSessionManager,
            "SessionState": SessionState,
        }
    elif name == "MobileStreamSession":
        from core.mobile.stream import MobileStreamSession

        values = {"MobileStreamSession": MobileStreamSession}
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    globals().update(values)
    return values[name]

__all__ = [
    "MobileDevice",
    "DeviceCapabilityError",
    "MobileDeviceManager",
    "MobileSessionManager",
    "SessionState",
    "MobileStreamSession",
]
