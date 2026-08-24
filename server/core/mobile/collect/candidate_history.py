"""In-memory index for skipping previously collected mobile candidates."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


_MIN_TITLE_ACCOUNT_LENGTH = 6
_MIN_TITLE_ONLY_LENGTH = 8
_MIN_CONTAINED_TITLE_ACCOUNT_LENGTH = 28
_MIN_TRUNCATED_TITLE_ONLY_LENGTH = 32
_TITLE_LABEL_PREFIX = re.compile(
    r"^\s*(?:项目名称|文章标题|标题)\s*[:：]\s*",
    re.IGNORECASE,
)
_TRUNCATION_MARKER = re.compile(r"(?:\.{2,}|…+|⋯+)")


def _normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        character
        for character in normalized
        if character.isalnum() or "\u4e00" <= character <= "\u9fff"
    )


def _normalize_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw.casefold()
    host = (parsed.hostname or "").casefold()
    if not host:
        return raw.casefold()
    try:
        port = parsed.port
    except ValueError:
        return raw.casefold()
    netloc = host if port is None else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    # WeChat /s/<token> paths are immutable identities; tracking query strings
    # only create false misses when the same article is shared again.
    query = "" if host == "mp.weixin.qq.com" and path.startswith("/s/") else parsed.query
    return urlunsplit(((parsed.scheme or "https").casefold(), netloc, path, query, ""))


def _title_variants(value: Any) -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    return {
        normalized
        for candidate in (raw, _TITLE_LABEL_PREFIX.sub("", raw))
        if (normalized := _normalize_text(candidate))
    }


def _account_identity(fields: dict[str, Any]) -> str:
    return _normalize_text(fields.get("account") or fields.get("author"))


def _is_truncated_title(value: Any) -> bool:
    return bool(_TRUNCATION_MARKER.search(str(value or "")))


def _has_contained_title(
    candidates: Iterable[str],
    known: Iterable[str],
    *,
    minimum_length: int,
) -> bool:
    known_titles = tuple(known)
    for candidate in candidates:
        if len(candidate) < minimum_length:
            continue
        for existing in known_titles:
            if min(len(candidate), len(existing)) < minimum_length:
                continue
            if candidate in existing or existing in candidate:
                return True
    return False


@dataclass(slots=True)
class CandidateHistory:
    """Compact Target-scoped history used before opening a mobile detail page."""

    title_keys: set[str] = field(default_factory=set)
    title_account_keys: set[tuple[str, str]] = field(default_factory=set)
    truncated_title_keys: set[str] = field(default_factory=set)
    truncated_title_account_keys: set[tuple[str, str]] = field(default_factory=set)
    source_urls: set[str] = field(default_factory=set)

    @classmethod
    def from_records(cls, records: Iterable[dict[str, Any]]) -> "CandidateHistory":
        history = cls()
        for record in records:
            history.add(
                dict(record.get("fields") or {}),
                source_url=record.get("source_url"),
            )
            discovery_fields = dict(record.get("discovery_fields") or {})
            if discovery_fields:
                history.add(discovery_fields)
        return history

    def add(
        self,
        fields: dict[str, Any],
        *,
        source_url: Any = None,
    ) -> None:
        raw_title = fields.get("title")
        titles = _title_variants(raw_title)
        account = _account_identity(fields)
        for title in titles:
            if len(title) >= _MIN_TITLE_ONLY_LENGTH:
                self.title_keys.add(title)
            if len(title) >= _MIN_TITLE_ACCOUNT_LENGTH and account:
                self.title_account_keys.add((title, account))
            if _is_truncated_title(raw_title):
                self.truncated_title_keys.add(title)
                if account:
                    self.truncated_title_account_keys.add((title, account))
        normalized_url = _normalize_url(source_url)
        if normalized_url:
            self.source_urls.add(normalized_url)

    def match(
        self,
        fields: dict[str, Any],
        *,
        source_url: Any = None,
    ) -> str | None:
        normalized_url = _normalize_url(source_url)
        if normalized_url and normalized_url in self.source_urls:
            return "原文链接已归档"
        raw_title = fields.get("title")
        titles = _title_variants(raw_title)
        account = _account_identity(fields)
        if account:
            if any(
                len(title) >= _MIN_TITLE_ACCOUNT_LENGTH
                and (title, account) in self.title_account_keys
                for title in titles
            ):
                return "相同标题和公众号已采集"
        if any(
            len(title) >= _MIN_TITLE_ONLY_LENGTH and title in self.title_keys
            for title in titles
        ):
            return "相同文章标题已采集"
        if account and _has_contained_title(
            titles,
            (
                title
                for title, known_account in self.title_account_keys
                if known_account == account
            ),
            minimum_length=_MIN_CONTAINED_TITLE_ACCOUNT_LENGTH,
        ):
            return "同公众号的长标题对应已采集"

        if _is_truncated_title(raw_title):
            if _has_contained_title(
                titles,
                self.title_keys,
                minimum_length=_MIN_TRUNCATED_TITLE_ONLY_LENGTH,
            ):
                return "截断标题对应文章已采集"
        else:
            if _has_contained_title(
                titles,
                self.truncated_title_keys,
                minimum_length=_MIN_TRUNCATED_TITLE_ONLY_LENGTH,
            ):
                return "截断标题对应文章已采集"
        return None


__all__ = ["CandidateHistory"]
