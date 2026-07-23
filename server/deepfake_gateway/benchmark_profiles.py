"""Benchmark FaceFusion profile candidates on a repeatable video sample.

Run this inside the GPU image. The output is JSON so profile decisions can be
compared without relying on a single frame or subjective timing.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import cv2
import numpy


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-path", default="/opt/facefusion/facefusion.ini")
    parser.add_argument("--source", default="/opt/facefusion/.assets/examples/source.jpg")
    parser.add_argument("--target", default="/opt/facefusion/.assets/examples/target-720p.mp4")
    parser.add_argument("--model", default="hyperswap_1a_256")
    parser.add_argument("--pixel-boost", default="512x512")
    parser.add_argument("--detector-model", default="yolo_face")
    parser.add_argument("--detector-size", default="640x640")
    parser.add_argument("--landmarker-model", default="2dfan4")
    parser.add_argument("--mask-types", nargs="+", default=["box"])
    parser.add_argument("--enhancer-model", default="")
    parser.add_argument("--enhancer-blend", type=int, default=60)
    parser.add_argument("--enhancer-weight", type=float, default=0.5)
    parser.add_argument("--swapper-weight", type=float, default=0.65)
    parser.add_argument("--providers", nargs="+", default=["cuda"])
    parser.add_argument("--max-width", type=int, default=640)
    parser.add_argument("--frames", type=int, default=30)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--jpeg-quality", type=int, default=88)
    parser.add_argument("--output-dir", default="")
    return parser.parse_args()


def _fit_width(frame: numpy.ndarray[Any, Any], max_width: int) -> numpy.ndarray[Any, Any]:
    height, width = frame.shape[:2]
    if width <= max_width:
        return frame
    ratio = max_width / width
    return cv2.resize(frame, (max_width, max(64, round(height * ratio))), interpolation=cv2.INTER_AREA)


def _read_video_frames(path: str, *, count: int, stride: int, max_width: int) -> list[numpy.ndarray[Any, Any]]:
    capture = cv2.VideoCapture(path)
    frames: list[numpy.ndarray[Any, Any]] = []
    index = 0
    try:
        while len(frames) < count:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            if index % stride == 0:
                frames.append(_fit_width(frame, max_width))
            index += 1
    finally:
        capture.release()
    if len(frames) < count:
        raise RuntimeError(f"Target video only yielded {len(frames)} usable frames")
    return frames


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _face_crop(frame: numpy.ndarray[Any, Any], face: Any) -> numpy.ndarray[Any, Any]:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(value)) for value in face.bounding_box]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    return frame[y1:y2, x1:x2]


def _sharpness(frame: numpy.ndarray[Any, Any], face: Any) -> float:
    crop = _face_crop(frame, face)
    if crop.size == 0:
        return 0.0
    return float(cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def _normalized_landmarks(face: Any) -> numpy.ndarray[Any, Any]:
    points = face.landmark_set.get("68")
    x1, y1, x2, y2 = [float(value) for value in face.bounding_box]
    scale = numpy.array([max(1.0, x2 - x1), max(1.0, y2 - y1)], dtype=numpy.float32)
    origin = numpy.array([x1, y1], dtype=numpy.float32)
    return (points.astype(numpy.float32) - origin) / scale


def _landmark_error(left: Any, right: Any) -> float:
    return float(numpy.linalg.norm(_normalized_landmarks(left) - _normalized_landmarks(right), axis=1).mean())


def _load_runtime(args: argparse.Namespace) -> tuple[list[Any], Any]:
    from facefusion import core, logger, state_manager
    from facefusion.args import apply_args
    from facefusion.processors.core import get_processors_modules
    from facefusion.program import create_program

    cli_args = vars(create_program().parse_args(["run", "--config-path", args.config_path]))
    apply_args(cli_args, state_manager.init_item)
    overrides = {
        "processors": ["face_swapper"] + (["face_enhancer"] if args.enhancer_model else []),
        "face_detector_model": args.detector_model,
        "face_detector_size": args.detector_size,
        "face_landmarker_model": args.landmarker_model,
        "face_mask_types": args.mask_types,
        "face_swapper_model": args.model,
        "face_swapper_pixel_boost": args.pixel_boost,
        "face_swapper_weight": args.swapper_weight,
        "execution_providers": args.providers,
        "execution_thread_count": 1,
        "log_level": "warn",
    }
    if args.enhancer_model:
        overrides.update(
            {
                "face_enhancer_model": args.enhancer_model,
                "face_enhancer_blend": args.enhancer_blend,
                "face_enhancer_weight": args.enhancer_weight,
            }
        )
    for key, value in overrides.items():
        state_manager.init_item(key, value)
    logger.init("warn")
    if not core.common_pre_check():
        raise RuntimeError("FaceFusion common model pre-check failed")
    modules = get_processors_modules(overrides["processors"])
    for module in modules:
        if not module.pre_check():
            raise RuntimeError(f"FaceFusion processor pre-check failed: {module.__name__}")
    return modules, state_manager


def _process_frame(
    modules: list[Any],
    source_frame: numpy.ndarray[Any, Any],
    target_frame: numpy.ndarray[Any, Any],
) -> numpy.ndarray[Any, Any]:
    from facefusion.audio import create_empty_audio_frame
    from facefusion.vision import extract_vision_mask

    output = target_frame.copy()
    output_mask = extract_vision_mask(output)
    audio = create_empty_audio_frame()
    for module in modules:
        output, output_mask = module.process_frame(
            {
                "source_vision_frames": [source_frame],
                "source_audio_frame": audio,
                "source_voice_frame": audio,
                "target_vision_frames": [target_frame],
                "temp_vision_frame": output,
                "temp_vision_mask": output_mask,
            }
        )
    return output


def _write_contact_sheet(
    output_dir: str,
    frames: list[numpy.ndarray[Any, Any]],
    outputs: list[numpy.ndarray[Any, Any]],
    label: str,
) -> str:
    if not output_dir:
        return ""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    samples = []
    for index in sorted({0, len(frames) // 2, len(frames) - 1}):
        samples.append(cv2.hconcat([frames[index], outputs[index]]))
    sheet = cv2.vconcat(samples)
    path = directory / f"{label}.jpg"
    cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 94])
    return str(path)


def main() -> None:
    args = _parse_args()
    modules, _ = _load_runtime(args)
    source = cv2.imread(args.source, cv2.IMREAD_COLOR)
    if source is None:
        raise RuntimeError("Source image could not be read")
    source = _fit_width(source, 1600)
    frames = _read_video_frames(
        args.target,
        count=args.frames + args.warmup,
        stride=max(1, args.stride),
        max_width=args.max_width,
    )

    for frame in frames[: args.warmup]:
        _process_frame(modules, source, frame)

    from facefusion.face_creator import get_static_faces
    from facefusion.face_selector import sort_faces_by_order

    source_faces = sort_faces_by_order(get_static_faces([source]), "large-small")
    if not source_faces:
        raise RuntimeError("No source face detected")
    source_face = source_faces[0]

    timings_ms: list[float] = []
    encode_ms: list[float] = []
    identity_scores: list[float] = []
    target_identity_scores: list[float] = []
    landmark_errors: list[float] = []
    sharpness_scores: list[float] = []
    output_embeddings: list[numpy.ndarray[Any, Any]] = []
    benchmark_frames = frames[args.warmup :]
    outputs = []
    for target_frame in benchmark_frames:
        started = time.perf_counter()
        output = _process_frame(modules, source, target_frame)
        timings_ms.append((time.perf_counter() - started) * 1000)
        encode_started = time.perf_counter()
        cv2.imencode(".jpg", output, [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality])
        encode_ms.append((time.perf_counter() - encode_started) * 1000)
        outputs.append(output)

        target_faces = sort_faces_by_order(get_static_faces([target_frame]), "large-small")
        output_faces = sort_faces_by_order(get_static_faces([output]), "large-small")
        if not target_faces or not output_faces:
            continue
        target_face, output_face = target_faces[0], output_faces[0]
        identity_scores.append(float(numpy.dot(source_face.embedding_norm, output_face.embedding_norm)))
        target_identity_scores.append(float(numpy.dot(target_face.embedding_norm, output_face.embedding_norm)))
        landmark_errors.append(_landmark_error(target_face, output_face))
        sharpness_scores.append(_sharpness(output, output_face))
        output_embeddings.append(output_face.embedding_norm)

    adjacent_identity = [
        float(numpy.dot(left, right))
        for left, right in zip(output_embeddings, output_embeddings[1:])
    ]
    label = "-".join(
        [
            args.model,
            args.pixel_boost,
            args.detector_model,
            args.detector_size,
            args.landmarker_model,
            "_".join(args.mask_types),
            f"weight_{args.swapper_weight:.2f}",
            args.enhancer_model or "none",
        ]
    )
    payload = {
        "label": label,
        "configuration": {
            "model": args.model,
            "pixel_boost": args.pixel_boost,
            "detector_model": args.detector_model,
            "detector_size": args.detector_size,
            "landmarker_model": args.landmarker_model,
            "mask_types": args.mask_types,
            "swapper_weight": args.swapper_weight,
            "enhancer_model": args.enhancer_model or None,
            "enhancer_blend": args.enhancer_blend if args.enhancer_model else None,
            "providers": args.providers,
            "max_width": args.max_width,
            "frames": args.frames,
        },
        "performance": {
            "mean_ms": round(statistics.mean(timings_ms), 2),
            "p50_ms": round(statistics.median(timings_ms), 2),
            "p95_ms": round(_percentile(timings_ms, 0.95), 2),
            "inference_fps": round(1000 / statistics.mean(timings_ms), 2),
            "jpeg_mean_ms": round(statistics.mean(encode_ms), 2),
        },
        "quality": {
            "measured_frames": len(identity_scores),
            "source_identity_mean": round(statistics.mean(identity_scores), 4) if identity_scores else None,
            "source_identity_std": round(statistics.pstdev(identity_scores), 4) if len(identity_scores) > 1 else 0,
            "target_identity_mean": round(statistics.mean(target_identity_scores), 4) if target_identity_scores else None,
            "landmark_error_mean": round(statistics.mean(landmark_errors), 4) if landmark_errors else None,
            "face_sharpness_mean": round(statistics.mean(sharpness_scores), 2) if sharpness_scores else None,
            "adjacent_identity_mean": round(statistics.mean(adjacent_identity), 4) if adjacent_identity else None,
            "adjacent_identity_std": round(statistics.pstdev(adjacent_identity), 4) if len(adjacent_identity) > 1 else 0,
        },
        "contact_sheet": _write_contact_sheet(args.output_dir, benchmark_frames, outputs, label),
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
