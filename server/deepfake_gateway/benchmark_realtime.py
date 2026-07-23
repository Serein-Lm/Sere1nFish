"""Measure the gateway's realtime API with browser-equivalent JPEG frames."""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import statistics
import time
from pathlib import Path
from typing import Any

import cv2
import httpx
import websockets


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://127.0.0.1:8443")
    parser.add_argument("--token-file", default="/run/secrets/deepfake_api_token")
    parser.add_argument("--source", default="/opt/facefusion/.assets/examples/source.jpg")
    parser.add_argument("--target", default="/opt/facefusion/.assets/examples/target-720p.mp4")
    parser.add_argument("--profile", default="fast")
    parser.add_argument("--max-width", type=int, default=640)
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--jpeg-quality", type=int, default=92)
    return parser.parse_args()


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _read_frames(args: argparse.Namespace) -> tuple[list[bytes], list[float]]:
    capture = cv2.VideoCapture(args.target)
    payloads: list[bytes] = []
    encode_ms: list[float] = []
    try:
        for _ in range(max(0, args.start_frame)):
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError(
                    f"Target video ended before start frame {args.start_frame}"
                )
        while len(payloads) < args.frames:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            height, width = frame.shape[:2]
            if width > args.max_width:
                ratio = args.max_width / width
                frame = cv2.resize(
                    frame,
                    (args.max_width, max(64, round(height * ratio))),
                    interpolation=cv2.INTER_AREA,
                )
            started = time.perf_counter()
            encoded_ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality],
            )
            encode_ms.append((time.perf_counter() - started) * 1000)
            if not encoded_ok:
                raise RuntimeError("Unable to encode input frame")
            payloads.append(encoded.tobytes())
    finally:
        capture.release()
    if len(payloads) < args.frames:
        raise RuntimeError(f"Target video only yielded {len(payloads)} usable frames")
    return payloads, encode_ms


async def _delete_session(client: httpx.AsyncClient, session_id: str) -> None:
    for _ in range(5):
        response = await client.delete(f"/v1/sessions/{session_id}")
        if response.status_code != 409:
            response.raise_for_status()
            return
        await asyncio.sleep(0.1)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    headers = {"Authorization": f"Bearer {token}"}
    source = Path(args.source)
    frame_payloads, encode_ms = _read_frames(args)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    session_id = ""

    async with httpx.AsyncClient(
        base_url=args.base_url,
        headers=headers,
        verify=False,
        timeout=120,
    ) as client:
        with source.open("rb") as source_file:
            response = await client.post(
                "/v1/sessions",
                files={"source": (source.name, source_file, "application/octet-stream")},
                data={
                    "authorized_use": "true",
                    "max_width": str(args.max_width),
                    "profile": args.profile,
                },
            )
        response.raise_for_status()
        created = response.json()
        session_id = str(created["session_id"])
        websocket_url = args.base_url.replace("https://", "wss://", 1)
        websocket_url += str(created["websocket_path"])
        roundtrip_ms: list[float] = []
        output_sizes: list[int] = []

        try:
            async with websockets.connect(
                websocket_url,
                additional_headers=headers,
                subprotocols=["sere1nfish"],
                ssl=ssl_context,
                max_size=8 * 1024 * 1024,
                ping_interval=None,
            ) as websocket:
                ready = json.loads(await websocket.recv())
                if ready.get("type") != "ready":
                    raise RuntimeError("Gateway did not acknowledge the realtime session")
                for payload in frame_payloads:
                    started = time.perf_counter()
                    await websocket.send(payload)
                    output = await websocket.recv()
                    roundtrip_ms.append((time.perf_counter() - started) * 1000)
                    if not isinstance(output, bytes):
                        raise RuntimeError(f"Gateway returned a non-frame message: {output}")
                    output_sizes.append(len(output))
                status_response = await client.get(f"/v1/sessions/{session_id}")
                status_response.raise_for_status()
                session_status = status_response.json()
        finally:
            await _delete_session(client, session_id)

    return {
        "configuration": {
            "profile": args.profile,
            "max_width": args.max_width,
            "frames": args.frames,
            "start_frame": args.start_frame,
            "jpeg_quality": args.jpeg_quality,
        },
        "client": {
            "input_encode_mean_ms": round(statistics.mean(encode_ms), 2),
            "input_bytes_mean": round(statistics.mean(map(len, frame_payloads))),
            "output_bytes_mean": round(statistics.mean(output_sizes)),
            "roundtrip_mean_ms": round(statistics.mean(roundtrip_ms), 2),
            "roundtrip_p50_ms": round(statistics.median(roundtrip_ms), 2),
            "roundtrip_p95_ms": round(_percentile(roundtrip_ms, 0.95), 2),
            "first_frame_ms": round(roundtrip_ms[0], 2),
            "steady_mean_ms": round(
                statistics.mean(roundtrip_ms[1:] or roundtrip_ms),
                2,
            ),
            "slowest_frame_ms": round(max(roundtrip_ms), 2),
            "effective_fps": round(1000 / statistics.mean(roundtrip_ms), 2),
        },
        "server": session_status,
    }


def main() -> None:
    args = _parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
