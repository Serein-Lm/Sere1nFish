"""Registry and factory for full-duplex voice providers."""

from __future__ import annotations

from collections.abc import Callable

from .adapters import BailianQwenAudioRealtimeProvider
from .contracts import RealtimeVoiceConfig, RealtimeVoiceProvider


class RealtimeVoiceProviderFactory:
    _registry: dict[
        str,
        Callable[[RealtimeVoiceConfig], RealtimeVoiceProvider],
    ] = {}

    @classmethod
    def register(
        cls,
        name: str,
        builder: Callable[[RealtimeVoiceConfig], RealtimeVoiceProvider],
    ) -> None:
        cls._registry[name] = builder

    @classmethod
    def create(cls, config: RealtimeVoiceConfig) -> RealtimeVoiceProvider:
        try:
            builder = cls._registry[config.provider]
        except KeyError as exc:
            raise ValueError(
                f"不支持的全双工语音 Provider: {config.provider}"
            ) from exc
        return builder(config)


RealtimeVoiceProviderFactory.register(
    BailianQwenAudioRealtimeProvider.name,
    BailianQwenAudioRealtimeProvider,
)
