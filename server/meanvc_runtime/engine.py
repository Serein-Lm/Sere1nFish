"""Stateful streaming adapter around the upstream MeanVC inference models."""

from __future__ import annotations

import io
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import librosa
import numpy
import soundfile
import torch
import torch.nn as nn
import torchaudio.compliance.kaldi as kaldi
from librosa.filters import mel as librosa_mel_fn

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 3200
INITIAL_CONTEXT_SAMPLES = 720
OUTPUT_SAMPLE_WIDTH = 2


def _load_upstream_speaker_factory() -> Any:
    source_root = Path(os.getenv("MEANVC_SOURCE_ROOT", "/opt/meanvc")).resolve()
    if not source_root.is_dir():
        raise RuntimeError(f"MeanVC source directory is unavailable: {source_root}")
    source_value = str(source_root)
    if source_value not in sys.path:
        sys.path.insert(0, source_value)
    from src.runtime.speaker_verification.verification import init_model

    return init_model


def decode_reference_audio(
    payload: bytes,
    *,
    minimum_seconds: float,
    maximum_seconds: float,
) -> numpy.ndarray[Any, numpy.dtype[numpy.float32]]:
    if not payload:
        raise ValueError("Target voice sample is empty")
    try:
        waveform, sample_rate = soundfile.read(io.BytesIO(payload), dtype="float32", always_2d=False)
    except (RuntimeError, TypeError, ValueError) as exc:
        raise ValueError("Target voice sample is not a supported audio file") from exc
    if waveform.ndim == 2:
        waveform = waveform.mean(axis=1)
    if waveform.ndim != 1 or not waveform.size:
        raise ValueError("Target voice sample contains no readable mono audio")
    if sample_rate != SAMPLE_RATE:
        waveform = librosa.resample(
            waveform,
            orig_sr=sample_rate,
            target_sr=SAMPLE_RATE,
            res_type="soxr_hq",
        )
    waveform, _ = librosa.effects.trim(waveform, top_db=38)
    minimum_samples = round(minimum_seconds * SAMPLE_RATE)
    maximum_samples = round(maximum_seconds * SAMPLE_RATE)
    if waveform.size < minimum_samples:
        raise ValueError(f"Target voice sample must contain at least {minimum_seconds:g} seconds of speech")
    waveform = numpy.asarray(waveform[:maximum_samples], dtype=numpy.float32)
    peak = float(numpy.max(numpy.abs(waveform)))
    if not numpy.isfinite(peak) or peak <= 1e-4:
        raise ValueError("Target voice sample is silent")
    return numpy.clip(waveform * min(0.95 / peak, 4.0), -1.0, 1.0)


def pcm16_to_float(payload: bytes) -> numpy.ndarray[Any, numpy.dtype[numpy.float32]]:
    aligned = payload[: len(payload) - (len(payload) % OUTPUT_SAMPLE_WIDTH)]
    return numpy.frombuffer(aligned, dtype="<i2").astype(numpy.float32) / 32768.0


def float_to_pcm16(waveform: numpy.ndarray[Any, Any]) -> bytes:
    normalized = numpy.nan_to_num(waveform, nan=0.0, posinf=1.0, neginf=-1.0)
    return (numpy.clip(normalized, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def _amp_to_db(value: torch.Tensor, min_level_db: int) -> torch.Tensor:
    minimum = torch.full_like(value, numpy.exp(min_level_db / 20 * numpy.log(10)))
    return 20 * torch.log10(torch.maximum(minimum, value))


def _normalize(value: torch.Tensor, max_abs_value: int, min_db: int) -> torch.Tensor:
    scaled = (2 * max_abs_value) * ((value - min_db) / (-min_db)) - max_abs_value
    return torch.clamp(scaled, -max_abs_value, max_abs_value)


class MelSpectrogramFeatures(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.n_fft = 1024
        self.hop_length = 160
        self.win_size = 640
        self.n_mels = 80
        self.fmax = 8000
        self.mel_basis: dict[str, torch.Tensor] = {}
        self.hann_window: dict[str, torch.Tensor] = {}

    def forward(self, waveform: torch.Tensor) -> torch.Tensor:
        device_key = f"{waveform.dtype}_{waveform.device}"
        mel_key = f"{self.fmax}_{device_key}"
        window_key = f"{self.win_size}_{device_key}"
        if mel_key not in self.mel_basis:
            mel = librosa_mel_fn(
                sr=SAMPLE_RATE,
                n_fft=self.n_fft,
                n_mels=self.n_mels,
                fmin=0,
                fmax=self.fmax,
            )
            self.mel_basis[mel_key] = torch.from_numpy(mel).to(
                dtype=waveform.dtype,
                device=waveform.device,
            )
        if window_key not in self.hann_window:
            self.hann_window[window_key] = torch.hann_window(self.win_size).to(
                dtype=waveform.dtype,
                device=waveform.device,
            )
        spectrum = torch.stft(
            waveform,
            self.n_fft,
            hop_length=self.hop_length,
            win_length=self.win_size,
            window=self.hann_window[window_key],
            center=True,
            pad_mode="reflect",
            normalized=False,
            onesided=True,
            return_complex=True,
        ).abs().clamp_min(1e-6)
        mel_spectrum = torch.matmul(self.mel_basis[mel_key], spectrum)
        return _normalize(_amp_to_db(mel_spectrum, -115) - 20, 1, -115)


def _extract_fbanks(waveform: numpy.ndarray[Any, Any]) -> torch.Tensor:
    tensor = torch.from_numpy(waveform * 32768.0).unsqueeze(0)
    return kaldi.fbank(
        tensor,
        frame_length=25,
        frame_shift=10,
        snip_edges=True,
        num_mel_bins=80,
        energy_floor=0.0,
        dither=0.0,
        sample_frequency=SAMPLE_RATE,
    ).unsqueeze(0)


@dataclass(slots=True)
class MeanVCStreamState:
    speaker_embedding: torch.Tensor
    prompt_mel: torch.Tensor
    steps: int
    samples_cache: numpy.ndarray[Any, Any] | None = None
    attention_cache: torch.Tensor | None = None
    convolution_cache: torch.Tensor | None = None
    asr_offset: int = 0
    encoder_output_cache: torch.Tensor | None = None
    vc_offset: int = 0
    vc_cache: torch.Tensor | None = None
    vc_kv_cache: Any = None
    vocoder_cache: torch.Tensor | None = None
    last_waveform: numpy.ndarray[Any, Any] | None = None
    needs_initial_context: bool = True
    chunks: int = 0
    total_inference_ms: float = 0.0
    last_inference_ms: float = 0.0

    @property
    def required_samples(self) -> int:
        return CHUNK_SAMPLES + (INITIAL_CONTEXT_SAMPLES if self.needs_initial_context else 0)


class MeanVCEngine:
    """Shares immutable models while keeping streaming caches per session."""

    def __init__(self, *, model_dir: Path, device: str, cpu_threads: int) -> None:
        self.model_dir = model_dir
        self.requested_device = device
        self.device = torch.device(
            "cuda"
            if device == "auto" and torch.cuda.is_available()
            else "cpu"
            if device == "auto"
            else device
        )
        if self.device.type == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("MeanVC CUDA execution was requested but CUDA is unavailable")
        torch.set_num_threads(max(1, cpu_threads))
        self.mel_extractor = MelSpectrogramFeatures().to(self.device)
        self.speaker_model: nn.Module | None = None
        self.asr: Any = None
        self.vc: Any = None
        self.vocoder: Any = None
        self.initialized = False
        self.started_at = 0.0
        self.warmup_ms = 0.0
        self._execution_lock = threading.Lock()

    def initialize(self) -> None:
        if self.initialized:
            return
        if self.device.type == "cuda":
            # The pinned Vocos TorchScript graph contains complex operations that
            # PyTorch's TensorExpr fuser cannot lower on CUDA.
            torch._C._jit_set_texpr_fuser_enabled(False)
            torch._C._jit_override_can_fuse_on_gpu(False)
        for filename in (
            "fastu2++.pt",
            "meanvc_200ms.pt",
            "vocos.pt",
            "wavlm_large_finetune.pth",
        ):
            path = self.model_dir / filename
            if not path.is_file():
                raise RuntimeError(f"MeanVC model asset is unavailable: {path}")
        init_speaker_model = _load_upstream_speaker_factory()
        speaker_model = init_speaker_model(
            "wavlm_large",
            str(self.model_dir / "wavlm_large_finetune.pth"),
        )
        self.speaker_model = speaker_model.eval().to(self.device)
        self.asr = torch.jit.load(
            str(self.model_dir / "fastu2++.pt"),
            map_location=self.device,
        ).eval()
        self.vc = torch.jit.load(
            str(self.model_dir / "meanvc_200ms.pt"),
            map_location=self.device,
        ).eval()
        self.vocoder = torch.jit.load(
            str(self.model_dir / "vocos.pt"),
            map_location=self.device,
        ).eval()
        self.initialized = True
        warmup_started = time.perf_counter()
        try:
            self._warm_up()
        except Exception:
            self.initialized = False
            raise
        self.warmup_ms = (time.perf_counter() - warmup_started) * 1000
        self.started_at = time.time()

    def _warm_up(self) -> None:
        example_path = (
            Path(os.getenv("MEANVC_SOURCE_ROOT", "/opt/meanvc"))
            / "src"
            / "runtime"
            / "example"
            / "test.wav"
        )
        waveform, sample_rate = soundfile.read(
            example_path,
            dtype="float32",
            always_2d=False,
        )
        if sample_rate != SAMPLE_RATE or waveform.ndim != 1:
            raise RuntimeError("MeanVC bundled warm-up sample has an unexpected format")
        required = CHUNK_SAMPLES * 2 + INITIAL_CONTEXT_SAMPLES
        if waveform.size < required:
            raise RuntimeError("MeanVC bundled warm-up sample is too short")
        for steps in (2, 1):
            stream = self.create_stream(waveform, steps=steps)
            self.process(stream, waveform[: stream.required_samples])
            offset = CHUNK_SAMPLES + INITIAL_CONTEXT_SAMPLES
            self.process(stream, waveform[offset:offset + stream.required_samples])

    def create_stream(self, reference: numpy.ndarray[Any, Any], *, steps: int) -> MeanVCStreamState:
        if not self.initialized or self.speaker_model is None:
            raise RuntimeError("MeanVC runtime is not initialized")
        if steps not in {1, 2}:
            raise ValueError("MeanVC inference steps must be 1 or 2")
        with self._execution_lock, torch.inference_mode():
            waveform = torch.from_numpy(reference).unsqueeze(0).to(self.device)
            speaker_embedding = self.speaker_model(waveform)
            prompt_mel = self.mel_extractor(waveform).transpose(1, 2)
        return MeanVCStreamState(
            speaker_embedding=speaker_embedding,
            prompt_mel=prompt_mel,
            steps=steps,
            attention_cache=torch.zeros((0, 0, 0, 0), device=self.device),
            convolution_cache=torch.zeros((0, 0, 0, 0), device=self.device),
        )

    def process(
        self,
        state: MeanVCStreamState,
        samples: numpy.ndarray[Any, numpy.dtype[numpy.float32]],
    ) -> bytes:
        if samples.size != state.required_samples:
            raise ValueError(
                f"MeanVC expected {state.required_samples} samples, received {samples.size}"
            )
        started = time.perf_counter()
        with self._execution_lock, torch.inference_mode():
            output = self._process_locked(state, samples)
        elapsed_ms = (time.perf_counter() - started) * 1000
        state.chunks += 1
        state.last_inference_ms = elapsed_ms
        state.total_inference_ms += elapsed_ms
        return float_to_pcm16(output)

    def _process_locked(
        self,
        state: MeanVCStreamState,
        samples: numpy.ndarray[Any, numpy.dtype[numpy.float32]],
    ) -> numpy.ndarray[Any, Any]:
        if state.samples_cache is not None:
            model_samples = numpy.concatenate((state.samples_cache, samples))
        else:
            model_samples = samples
        state.samples_cache = model_samples[-INITIAL_CONTEXT_SAMPLES:]
        state.needs_initial_context = False

        fbanks = _extract_fbanks(model_samples).float().to(self.device)
        encoder_output, state.attention_cache, state.convolution_cache = self.asr.forward_encoder_chunk(
            fbanks,
            state.asr_offset,
            10,
            state.attention_cache,
            state.convolution_cache,
        )
        state.asr_offset += encoder_output.size(1)
        if state.encoder_output_cache is None:
            encoder_output = torch.cat([encoder_output[:, 0:1, :], encoder_output], dim=1)
        else:
            encoder_output = torch.cat([state.encoder_output_cache, encoder_output], dim=1)
        state.encoder_output_cache = encoder_output[:, -1:, :]
        encoder_output = torch.nn.functional.interpolate(
            encoder_output.transpose(1, 2),
            size=21,
            mode="linear",
            align_corners=True,
        ).transpose(1, 2)[:, 1:, :]

        converted = torch.randn(
            1,
            encoder_output.shape[1],
            80,
            device=self.device,
            dtype=encoder_output.dtype,
        )
        timesteps = (
            torch.tensor([1.0, 0.0], device=self.device)
            if state.steps == 1
            else torch.tensor([1.0, 0.8, 0.0], device=self.device)
        )
        next_kv_cache: Any = None
        for index in range(state.steps):
            current = timesteps[index]
            following = timesteps[index + 1]
            velocity, next_kv_cache = self.vc(
                converted,
                current.expand(1),
                following.expand(1),
                cache=state.vc_cache,
                cond=encoder_output,
                spks=state.speaker_embedding,
                prompts=state.prompt_mel,
                offset=state.vc_offset,
                kv_cache=state.vc_kv_cache,
            )
            converted = converted - (current - following) * velocity
        state.vc_kv_cache = next_kv_cache
        state.vc_offset += converted.shape[1]
        state.vc_cache = converted

        if state.vc_offset > 40 and state.vc_kv_cache[0][0].shape[2] > 100:
            for index, (key, value) in enumerate(state.vc_kv_cache):
                state.vc_kv_cache[index] = (
                    key[:, :, -100:, :],
                    value[:, :, -100:, :],
                )
        if state.chunks and state.chunks % 50 == 0:
            state.asr_offset = 20
            state.vc_offset = 120

        mel = converted.transpose(1, 2)
        if state.vocoder_cache is not None:
            mel = torch.cat([state.vocoder_cache, mel], dim=-1)
        state.vocoder_cache = mel[:, :, -3:]
        waveform = self.vocoder.decode((mel + 1) / 2).squeeze().detach().float().cpu().numpy()
        overlap_samples = 320
        if state.last_waveform is None:
            output = waveform[:-overlap_samples]
        else:
            down = numpy.linspace(1.0, 0.0, overlap_samples, dtype=numpy.float32)
            up = numpy.linspace(0.0, 1.0, overlap_samples, dtype=numpy.float32)
            blended = state.last_waveform * down + waveform[:overlap_samples] * up
            output = numpy.concatenate(
                (blended, waveform[overlap_samples:-overlap_samples]),
            )
        state.last_waveform = waveform[-overlap_samples:]
        return numpy.asarray(output, dtype=numpy.float32)
