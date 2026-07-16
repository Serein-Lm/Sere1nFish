"""统一外部资产发现能力。"""

from .contracts import AssetCandidate, AssetIdentity, AssetProvider, ProviderSearchResult
from .service import AssetIntelligenceService

__all__ = [
    "AssetCandidate",
    "AssetIdentity",
    "AssetProvider",
    "ProviderSearchResult",
    "AssetIntelligenceService",
]
