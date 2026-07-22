"""Deepfake provider registry and factory."""

from __future__ import annotations

from collections.abc import Callable

from .adapters import FaceFusionGatewayProvider
from .contracts import DeepfakeConfig, DeepfakeProvider


class DeepfakeProviderFactory:
    _registry: dict[str, Callable[[DeepfakeConfig], DeepfakeProvider]] = {}

    @classmethod
    def register(
        cls,
        name: str,
        builder: Callable[[DeepfakeConfig], DeepfakeProvider],
    ) -> None:
        cls._registry[name] = builder

    @classmethod
    def create(cls, config: DeepfakeConfig) -> DeepfakeProvider:
        try:
            return cls._registry[config.provider](config)
        except KeyError as exc:
            raise ValueError(f"Unsupported Deepfake provider: {config.provider}") from exc

    @classmethod
    def available(cls) -> tuple[str, ...]:
        return tuple(cls._registry)


DeepfakeProviderFactory.register("facefusion_gateway", FaceFusionGatewayProvider)
