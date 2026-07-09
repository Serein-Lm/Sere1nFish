"""Control coordinate resolution for the main mobile API."""

from __future__ import annotations

from typing import Literal

from AutoGLM_GUI.control_coordinates import (
    COORD_SCALE_AGENT,
    COORD_SCALE_API,
    get_display_size,
    normalized_to_pixels,
    resolve_control_point as _resolve_auto,
    resolve_control_segment as _resolve_segment_auto,
)

CoordSpace = Literal["pixel", "normalized_1000", "normalized_10000", "auto"]


def resolve_tap(
    x: int,
    y: int,
    *,
    device_id: str,
    coord_space: CoordSpace = "pixel",
) -> tuple[int, int]:
    if coord_space == "pixel":
        return x, y
    if coord_space == "auto":
        return _resolve_auto(x, y, device_id=device_id)
    scale = (
        COORD_SCALE_AGENT
        if coord_space == "normalized_1000"
        else COORD_SCALE_API
    )
    width, height = get_display_size(device_id)
    return normalized_to_pixels(x, y, width=width, height=height, scale=scale)


def resolve_swipe(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    *,
    device_id: str,
    coord_space: CoordSpace = "pixel",
) -> tuple[int, int, int, int]:
    if coord_space == "pixel":
        return start_x, start_y, end_x, end_y
    if coord_space == "auto":
        return _resolve_segment_auto(
            start_x, start_y, end_x, end_y, device_id=device_id
        )
    scale = (
        COORD_SCALE_AGENT
        if coord_space == "normalized_1000"
        else COORD_SCALE_API
    )
    width, height = get_display_size(device_id)
    sx, sy = normalized_to_pixels(
        start_x, start_y, width=width, height=height, scale=scale
    )
    ex, ey = normalized_to_pixels(
        end_x, end_y, width=width, height=height, scale=scale
    )
    return sx, sy, ex, ey
