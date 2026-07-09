"""Normalize control API coordinates (0–1000 / 0–10000) to device pixels."""

from __future__ import annotations

import re
import subprocess

from AutoGLM_GUI.platform_utils import build_adb_command

COORD_SCALE_AGENT = 1000
COORD_SCALE_API = 10000
_DEFAULT_DISPLAY = (1080, 2400)

_WM_SIZE_RE = re.compile(r"(\d+)\s*x\s*(\d+)")


def get_display_size(
    device_id: str | None = None, adb_path: str = "adb", timeout: int = 10
) -> tuple[int, int]:
    """Read physical display size via ``adb shell wm size``."""
    cmd = build_adb_command(device_id, adb_path) + ["shell", "wm", "size"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return _DEFAULT_DISPLAY

    if result.returncode != 0 or not result.stdout:
        return _DEFAULT_DISPLAY

    matches = _WM_SIZE_RE.findall(result.stdout)
    if not matches:
        return _DEFAULT_DISPLAY

    w, h = matches[-1]
    width, height = int(w), int(h)
    if width <= 0 or height <= 0:
        return _DEFAULT_DISPLAY
    return width, height


def _detect_scale(*values: int) -> int:
    peak = max(values)
    if peak <= COORD_SCALE_AGENT:
        return COORD_SCALE_AGENT
    return COORD_SCALE_API


def normalized_to_pixels(
    x: int,
    y: int,
    *,
    width: int,
    height: int,
    scale: int | None = None,
) -> tuple[int, int]:
    """Map normalized coordinates to pixel coordinates."""
    denom = scale if scale is not None else _detect_scale(x, y)
    px = max(0, min(int(round(x / denom * width)), width - 1))
    py = max(0, min(int(round(y / denom * height)), height - 1))
    return px, py


def resolve_control_point(
    x: int,
    y: int,
    *,
    device_id: str | None,
    adb_path: str = "adb",
) -> tuple[int, int]:
    """Resolve a single control point for the given device."""
    width, height = get_display_size(device_id, adb_path=adb_path)
    return normalized_to_pixels(x, y, width=width, height=height)


def resolve_control_segment(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    *,
    device_id: str | None,
    adb_path: str = "adb",
) -> tuple[int, int, int, int]:
    """Resolve swipe / drag endpoints using one scale for the whole gesture."""
    width, height = get_display_size(device_id, adb_path=adb_path)
    scale = _detect_scale(start_x, start_y, end_x, end_y)
    sx, sy = normalized_to_pixels(
        start_x, start_y, width=width, height=height, scale=scale
    )
    ex, ey = normalized_to_pixels(end_x, end_y, width=width, height=height, scale=scale)
    return sx, sy, ex, ey
