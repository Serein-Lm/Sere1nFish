import pytest

from deepfake_gateway.media_pipeline import fit_even_dimensions, parse_frame_rate


def test_fit_even_dimensions_preserves_aspect_ratio_and_encoder_constraints() -> None:
    assert fit_even_dimensions(1920, 1080, 640) == (640, 360)
    assert fit_even_dimensions(641, 481, 640) == (640, 480)
    assert fit_even_dimensions(320, 241, 640) == (320, 240)


def test_fit_even_dimensions_rejects_invalid_video() -> None:
    with pytest.raises(ValueError):
        fit_even_dimensions(0, 1080, 640)


def test_parse_frame_rate_handles_fractional_and_invalid_values() -> None:
    assert parse_frame_rate("30000/1001") == pytest.approx(29.970, rel=0.001)
    assert parse_frame_rate("15/1") == 15
    assert parse_frame_rate("0/0") == 0
    assert parse_frame_rate("not-a-rate") == 0
