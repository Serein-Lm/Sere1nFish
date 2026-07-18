"""外部资产情报统一协议与领域模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit


@dataclass(slots=True)
class AssetIdentity:
    input_name: str
    normalized_name: str
    root_domain: str
    target_id: str = ""
    aliases: list[str] = field(default_factory=list)
    root_domains: list[str] = field(default_factory=list)

    @property
    def domains(self) -> list[str]:
        return list(
            dict.fromkeys(
                value.strip().lower()
                for value in [self.root_domain, *self.root_domains]
                if isinstance(value, str) and value.strip()
            )
        )[:6]


def canonical_asset_url(value: str, *, protocol: str = "", port: str = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    scheme = "https" if str(protocol).lower() in {"https", "ssl", "tls"} else "http"
    if not raw.startswith(("http://", "https://")):
        raw = f"{scheme}://{raw}"
    try:
        parsed = urlsplit(raw)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        if not hostname:
            return ""
        parsed_port = parsed.port
    except ValueError:
        return ""
    resolved_port = str(parsed_port or port or "").strip()
    default_port = (parsed.scheme == "http" and resolved_port in {"", "80"}) or (
        parsed.scheme == "https" and resolved_port in {"", "443"}
    )
    authority = hostname if default_port else f"{hostname}:{resolved_port}"
    return f"{parsed.scheme.lower()}://{authority}"


@dataclass(slots=True)
class AssetCandidate:
    host: str = ""
    ip: str = ""
    port: str = ""
    protocol: str = ""
    domain: str = ""
    title: str = ""
    link: str = ""
    cert_domain: str = ""
    fingerprints: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    source_queries: list[str] = field(default_factory=list)
    is_alive: bool | None = None
    probe: dict = field(default_factory=dict)

    @property
    def canonical_url(self) -> str:
        preferred = self.link or self.host or self.domain or self.ip
        return canonical_asset_url(preferred, protocol=self.protocol, port=self.port)

    @property
    def endpoint_key(self) -> str:
        return self.canonical_url

    def merge(self, other: "AssetCandidate") -> None:
        for field_name in (
            "host",
            "ip",
            "port",
            "protocol",
            "domain",
            "title",
            "link",
            "cert_domain",
        ):
            if not getattr(self, field_name) and getattr(other, field_name):
                setattr(self, field_name, getattr(other, field_name))
        self.fingerprints = list(dict.fromkeys([*self.fingerprints, *other.fingerprints]))
        self.sources = list(dict.fromkeys([*self.sources, *other.sources]))
        self.source_queries = list(dict.fromkeys([*self.source_queries, *other.source_queries]))

    def as_dict(self, *, target_id: str = "") -> dict:
        canonical = self.canonical_url
        parsed = urlsplit(canonical) if canonical else None
        hostname = (parsed.hostname or "") if parsed else ""
        port = self.port
        if not port and parsed:
            port = str(parsed.port or (443 if parsed.scheme == "https" else 80))
        return {
            "host": hostname or self.host,
            "ip": self.ip,
            "port": str(port or ""),
            "protocol": self.protocol or (parsed.scheme if parsed else ""),
            "domain": self.domain,
            "title": self.title,
            "link": self.link or canonical,
            "canonical_url": canonical,
            "cert_domain": self.cert_domain,
            "fingerprints": self.fingerprints,
            "sources": self.sources,
            "source_queries": self.source_queries,
            "is_alive": self.is_alive,
            "probe": self.probe,
            "target_id": target_id,
        }


@dataclass(slots=True)
class ProviderSearchResult:
    provider: str
    candidates: list[AssetCandidate] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class AssetProvider(Protocol):
    name: str

    async def search(self, identity: AssetIdentity, *, size: int) -> ProviderSearchResult: ...
