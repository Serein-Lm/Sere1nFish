"""Low-latency RTSP bridge used by OBS WHIP/WHEP sessions.

MediaMTX terminates WebRTC. This module only handles the local media plane:
decode the newest available input frame, run the supplied inference callback,
and publish a steady H.264 stream. Keeping only the newest input frame prevents
GPU inference from accumulating latency when it is slower than the camera.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import Any, Awaitable, Callable

import numpy


class MediaPipelineError(RuntimeError):
    """Raised when a media subprocess cannot be started or exits unexpectedly."""


FrameProcessor = Callable[[numpy.ndarray[Any, Any]], Awaitable[tuple[numpy.ndarray[Any, Any], float]]]


@dataclass(slots=True)
class MediaPipelineStats:
    state: str = "waiting_input"
    width: int = 0
    height: int = 0
    source_fps: float = 0.0
    output_fps: int = 0
    input_frames: int = 0
    processed_frames: int = 0
    output_frames: int = 0
    dropped_frames: int = 0
    reconnects: int = 0
    last_inference_ms: float = 0.0
    last_error: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_fps"] = round(self.source_fps, 2)
        payload["last_inference_ms"] = round(self.last_inference_ms, 2)
        return payload


def fit_even_dimensions(width: int, height: int, max_width: int) -> tuple[int, int]:
    if width < 2 or height < 2:
        raise ValueError("Video dimensions are invalid")
    ratio = min(1.0, max_width / width)
    output_width = max(2, int(width * ratio))
    output_height = max(2, int(height * ratio))
    output_width -= output_width % 2
    output_height -= output_height % 2
    return output_width, output_height


def parse_frame_rate(value: str) -> float:
    try:
        rate = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return 0.0
    return rate if 0 < rate <= 240 else 0.0


class MediaPipeline:
    """Reconnectable latest-frame processor backed by FFmpeg subprocesses."""

    def __init__(
        self,
        *,
        input_url: str,
        output_url: str,
        max_width: int,
        output_fps: int,
        processor: FrameProcessor,
    ) -> None:
        self.input_url = input_url
        self.output_url = output_url
        self.max_width = max(320, min(1280, max_width))
        self.output_fps = max(5, min(30, output_fps))
        self.processor = processor
        self.stats = MediaPipelineStats(output_fps=self.output_fps)
        self._stopping = asyncio.Event()
        self._processes: set[asyncio.subprocess.Process] = set()

    async def run(self) -> None:
        while not self._stopping.is_set():
            self.stats.state = "waiting_input"
            stream = await self._probe_input()
            if stream is None:
                await self._sleep_or_stop(0.5)
                continue
            try:
                await self._run_stream(*stream)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stats.state = "reconnecting"
                self.stats.last_error = str(exc)[:300]
                self.stats.reconnects += 1
                await self._sleep_or_stop(1.0)
            finally:
                await self._terminate_processes()
        self.stats.state = "stopped"

    async def stop(self) -> None:
        self._stopping.set()
        await self._terminate_processes()

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _probe_input(self) -> tuple[int, int, float] | None:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-rtsp_transport",
            "tcp",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate",
            "-of",
            "json",
            self.input_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._processes.add(process)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=4.0)
        except TimeoutError:
            await self._terminate_process(process)
            return None
        finally:
            self._processes.discard(process)
        if process.returncode != 0:
            return None
        try:
            streams = json.loads(stdout).get("streams") or []
            stream = streams[0]
            width = int(stream["width"])
            height = int(stream["height"])
            fps = parse_frame_rate(str(stream.get("avg_frame_rate") or "0"))
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return width, height, fps

    async def _run_stream(self, source_width: int, source_height: int, source_fps: float) -> None:
        width, height = fit_even_dimensions(source_width, source_height, self.max_width)
        self.stats.width = width
        self.stats.height = height
        self.stats.source_fps = source_fps
        self.stats.last_error = ""

        decoder = await self._start_decoder(width, height)
        encoder = await self._start_encoder(width, height)
        frame_size = width * height * 3
        latest_input: bytes | None = None
        latest_input_sequence = 0
        latest_output: bytes | None = None
        input_event = asyncio.Event()
        output_event = asyncio.Event()

        async def read_frames() -> None:
            nonlocal latest_input, latest_input_sequence
            assert decoder.stdout is not None
            while not self._stopping.is_set():
                try:
                    frame = await decoder.stdout.readexactly(frame_size)
                except asyncio.IncompleteReadError as exc:
                    raise MediaPipelineError("OBS input stream ended") from exc
                previous = latest_input_sequence
                latest_input = frame
                latest_input_sequence += 1
                self.stats.input_frames += 1
                if previous and input_event.is_set():
                    self.stats.dropped_frames += 1
                input_event.set()

        async def process_frames() -> None:
            nonlocal latest_output
            processed_sequence = 0
            while not self._stopping.is_set():
                await input_event.wait()
                frame = latest_input
                sequence = latest_input_sequence
                input_event.clear()
                if frame is None or sequence == processed_sequence:
                    continue
                target = numpy.frombuffer(frame, dtype=numpy.uint8).reshape((height, width, 3)).copy()
                output, inference_ms = await self.processor(target)
                if output.shape[:2] != (height, width):
                    raise MediaPipelineError("Inference changed the video dimensions")
                latest_output = numpy.ascontiguousarray(output, dtype=numpy.uint8).tobytes()
                processed_sequence = sequence
                self.stats.processed_frames += 1
                self.stats.last_inference_ms = inference_ms
                output_event.set()

        async def write_frames() -> None:
            assert encoder.stdin is not None
            await output_event.wait()
            loop = asyncio.get_running_loop()
            interval = 1.0 / self.output_fps
            deadline = loop.time()
            while not self._stopping.is_set():
                frame = latest_output
                if frame is not None:
                    try:
                        encoder.stdin.write(frame)
                        await encoder.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError) as exc:
                        raise MediaPipelineError("GPU video encoder stopped") from exc
                    self.stats.output_frames += 1
                    self.stats.state = "live"
                deadline += interval
                await asyncio.sleep(max(0.0, deadline - loop.time()))

        tasks = [
            asyncio.create_task(read_frames(), name="media-read"),
            asyncio.create_task(process_frames(), name="media-inference"),
            asyncio.create_task(write_frames(), name="media-publish"),
        ]
        self.stats.state = "starting"
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                exception = task.exception()
                if exception:
                    raise exception
            if pending:
                raise MediaPipelineError("Media pipeline stopped unexpectedly")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _start_decoder(self, width: int, height: int) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-analyzeduration",
            "100000",
            "-probesize",
            "32768",
            "-i",
            self.input_url,
            "-map",
            "0:v:0",
            "-vf",
            f"fps={self.output_fps},scale={width}:{height}:flags=fast_bilinear",
            "-an",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=max(64 * 1024, width * height * 3 * 2),
        )
        self._processes.add(process)
        return process

    async def _start_encoder(self, width: int, height: int) -> asyncio.subprocess.Process:
        encoder = os.getenv("DEEPFAKE_MEDIA_ENCODER", "h264_nvenc").strip() or "h264_nvenc"
        bitrate = os.getenv("DEEPFAKE_MEDIA_VIDEO_BITRATE", "1800k").strip() or "1800k"
        codec_args = ["-c:v", encoder]
        if encoder == "h264_nvenc":
            codec_args.extend(["-preset", "p1", "-tune", "ull", "-rc", "cbr", "-zerolatency", "1"])
        else:
            codec_args.extend(["-preset", "ultrafast", "-tune", "zerolatency"])
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(self.output_fps),
            "-i",
            "pipe:0",
            "-an",
            *codec_args,
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "baseline",
            "-b:v",
            bitrate,
            "-maxrate",
            bitrate,
            "-bufsize",
            bitrate,
            "-g",
            str(self.output_fps),
            "-bf",
            "0",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            self.output_url,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._processes.add(process)
        return process

    async def _terminate_processes(self) -> None:
        processes = list(self._processes)
        self._processes.clear()
        await asyncio.gather(*(self._terminate_process(process) for process in processes))

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            process.kill()
            await process.wait()
