"""Shared contracts for information collection tools.

Pipeline stages should depend on these contracts instead of constructing
platform clients directly.  This keeps account pools, proxies, retries and
rate-control rules behind tool adapters that can evolve independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class SearchRequest:
    source: str
    query: str
    project_id: str
    task_id: str
    limit: int
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    source: str
    query: str
    items: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.items)


class SearchTool(Protocol):
    name: str

    async def search(self, request: SearchRequest) -> SearchResult:
        """Run one search request and return normalized items."""


@dataclass(frozen=True)
class ScanRequest:
    source: str
    target: str
    project_id: str
    task_id: str
    target_info: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScanResult:
    source: str
    target: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    raw: Any = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.success and bool(self.data)


class ScanTool(Protocol):
    name: str

    async def scan(self, request: ScanRequest) -> ScanResult:
        """Scan one target and return normalized structured data."""


@dataclass(frozen=True)
class ProbeRequest:
    source: str
    urls: list[str]
    project_id: str = ""
    task_id: str = ""
    concurrency: int = 20
    timeout: float = 10.0
    only_alive: bool = True
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProbeResult:
    source: str
    items: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.items)


class ProbeTool(Protocol):
    name: str

    async def probe(self, request: ProbeRequest) -> ProbeResult:
        """Probe a batch of URLs and return normalized reachable targets."""


@dataclass(frozen=True)
class DetailRequest:
    source: str
    item_id: str
    project_id: str
    task_id: str
    xsec_token: str = ""
    xsec_source: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class DetailResult:
    source: str
    item_id: str
    content: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    comments_summary: str = ""
    comments_data: list[dict[str, Any]] = field(default_factory=list)
    images_urls: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.raw or self.content or self.comments_data or self.images_urls)


class DetailTool(Protocol):
    name: str

    async def fetch_detail(self, request: DetailRequest) -> DetailResult:
        """Fetch normalized detail for one collected item."""


@dataclass(frozen=True)
class TagRequest:
    source: str
    kind: str
    item_id: str
    item: dict[str, Any]
    project_id: str = ""
    task_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class TagResult:
    source: str
    kind: str
    item_id: str
    tagging: dict[str, Any] = field(default_factory=dict)
    raw: Any = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> int:
        try:
            return int(self.tagging.get("attention_score", 0))
        except Exception:
            return 0

    @property
    def ok(self) -> bool:
        return bool(self.tagging)


class TaggingTool(Protocol):
    name: str

    async def tag(self, request: TagRequest) -> TagResult:
        """Analyze and tag one collected item."""


@dataclass(frozen=True)
class ProfileRequest:
    source: str
    project_id: str
    task_id: str
    keyword: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProfileResult:
    source: str
    project_id: str
    task_id: str
    profiles: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.profiles)


class ProfileTool(Protocol):
    name: str

    async def generate_profile(self, request: ProfileRequest) -> ProfileResult:
        """Generate profiles for collected entities."""


@dataclass(frozen=True)
class CopywritingRequest:
    source: str
    project_id: str
    task_id: str
    target_id: str
    context: str
    target: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class CopywritingResult:
    source: str
    project_id: str
    task_id: str
    target_id: str
    copywritings: list[dict[str, Any]] = field(default_factory=list)
    raw: Any = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.copywritings)

    @property
    def ok(self) -> bool:
        return bool(self.copywritings)


class CopywritingTool(Protocol):
    name: str

    async def generate(self, request: CopywritingRequest) -> CopywritingResult:
        """Generate copywriting candidates for one collected target."""
