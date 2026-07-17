"""公司控股结构 Provider 工厂。"""
from __future__ import annotations

from .adapters import TianyanchaControlProvider
from .contracts import CompanyControlProvider


class CompanyControlProviderFactory:
    @staticmethod
    async def create(provider: str = "tianyancha") -> CompanyControlProvider:
        normalized = str(provider or "").strip().lower()
        if normalized == "tianyancha":
            return await TianyanchaControlProvider.create()
        raise ValueError(f"不支持的公司控股结构 Provider: {provider}")
