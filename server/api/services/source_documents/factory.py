"""来源文档 Provider 注册表。"""
from __future__ import annotations

from .contracts import SourceDocumentProvider


_providers: list[SourceDocumentProvider] | None = None


def _load_defaults() -> list[SourceDocumentProvider]:
    from .wechat import WechatArticleProvider

    return [WechatArticleProvider()]


def list_source_document_providers() -> list[SourceDocumentProvider]:
    global _providers
    if _providers is None:
        _providers = _load_defaults()
    return list(_providers)


def register_source_document_provider(provider: SourceDocumentProvider) -> None:
    global _providers
    if _providers is None:
        _providers = _load_defaults()
    _providers = [item for item in _providers if item.source_type != provider.source_type]
    _providers.append(provider)


def get_source_document_provider(url: str) -> SourceDocumentProvider:
    for provider in list_source_document_providers():
        if provider.supports(url):
            return provider
    raise ValueError(f"没有支持该 URL 的来源文档 Provider: {url}")
