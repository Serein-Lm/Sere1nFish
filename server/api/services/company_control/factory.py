"""公司全资子公司 Provider 工厂。"""
from __future__ import annotations

from .adapters import TianyanchaInvestmentProvider
from .contracts import CompanyControlProvider


class CompanyControlProviderFactory:
    @staticmethod
    async def create(provider: str = "tianyancha") -> CompanyControlProvider:
        normalized = str(provider or "").strip().lower()
        if normalized == "tianyancha":
            return await TianyanchaInvestmentProvider.create()
        raise ValueError(f"不支持的公司全资子公司 Provider: {provider}")
