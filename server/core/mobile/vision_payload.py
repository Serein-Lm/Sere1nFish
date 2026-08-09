"""Canonical image payload adapter for mobile vision model calls."""

from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


_MEDIA_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def _rgb_frame(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def prepare_vision_data_url(
    image_base64: str,
    *,
    max_source_bytes: int = 1_250_000,
    max_output_bytes: int = 1_000_000,
    max_side: int = 2048,
) -> str:
    """Validate and bound a screenshot before sending it to a vision endpoint.

    The archived screenshot remains lossless.  Only the model payload is converted
    when a large PNG or unsupported/paletted image would make a fragile data URL.
    """
    payload = str(image_base64 or "").strip()
    if payload.startswith("data:"):
        if "," not in payload:
            raise ValueError("视觉图片 data URL 缺少内容")
        payload = payload.split(",", 1)[1]
    payload = "".join(payload.split())
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("视觉图片不是有效 Base64") from exc
    if not raw:
        raise ValueError("视觉图片为空")

    try:
        with Image.open(BytesIO(raw)) as source:
            image_format = str(source.format or "").upper()
            source.load()
            safe_original = (
                image_format in _MEDIA_TYPES
                and len(raw) <= max(64_000, int(max_source_bytes))
                and max(source.size or (0, 0)) <= max(256, int(max_side))
                and not (
                    source.mode == "P" and "transparency" in source.info
                )
            )
            if safe_original:
                return f"data:{_MEDIA_TYPES[image_format]};base64,{payload}"

            frame = _rgb_frame(source)
            frame.thumbnail(
                (max(256, int(max_side)), max(256, int(max_side))),
                Image.Resampling.LANCZOS,
            )
            output = BytesIO()
            output_limit = max(128_000, int(max_output_bytes))
            for quality in (88, 82, 76, 70, 64):
                output.seek(0)
                output.truncate(0)
                frame.save(output, format="JPEG", quality=quality, optimize=True)
                if output.tell() <= output_limit:
                    break
            while output.tell() > output_limit and max(frame.size) > 960:
                resized = frame.copy()
                resized.thumbnail(
                    (
                        max(960, int(frame.width * 0.85)),
                        max(960, int(frame.height * 0.85)),
                    ),
                    Image.Resampling.LANCZOS,
                )
                frame = resized
                output.seek(0)
                output.truncate(0)
                frame.save(output, format="JPEG", quality=70, optimize=True)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("视觉图片无法解码") from exc

    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
