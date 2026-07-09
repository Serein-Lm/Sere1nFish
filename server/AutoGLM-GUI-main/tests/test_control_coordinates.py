"""Unit tests for control coordinate normalization."""

from AutoGLM_GUI.control_coordinates import (
    COORD_SCALE_AGENT,
    COORD_SCALE_API,
    normalized_to_pixels,
)


def test_normalized_to_pixels_agent_scale() -> None:
    px, py = normalized_to_pixels(500, 500, width=1080, height=2400, scale=COORD_SCALE_AGENT)
    assert px == 540
    assert py == 1200


def test_normalized_to_pixels_api_scale() -> None:
    px, py = normalized_to_pixels(5000, 5000, width=1080, height=2400, scale=COORD_SCALE_API)
    assert px == 540
    assert py == 1200
