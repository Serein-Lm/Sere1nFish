"""资产 Provider 注册与工厂。"""

from __future__ import annotations

from collections.abc import Callable

from .adapters import FofaAssetProvider, HunterAssetProvider
from .contracts import AssetProvider


class AssetProviderFactory:
    _registry: dict[str, Callable[[], AssetProvider]] = {}

    @classmethod
    def register(cls, name: str, builder: Callable[[], AssetProvider]) -> None:
        cls._registry[name] = builder

    @classmethod
    def create(cls, name: str) -> AssetProvider:
        try:
            return cls._registry[name]()
        except KeyError as exc:
            raise ValueError(f"不支持的资产 Provider: {name}") from exc

    @classmethod
    def available(cls) -> tuple[str, ...]:
        return tuple(cls._registry)


AssetProviderFactory.register("fofa", FofaAssetProvider)
AssetProviderFactory.register("hunter", HunterAssetProvider)
