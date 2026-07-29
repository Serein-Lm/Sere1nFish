"""Compact browser runtime for source-grounded fictional persona research.

The model decides what to research through PersonaResearchMission. This adapter
executes the mechanical browser work with fixed, read-only Chrome MCP calls and
returns bounded page evidence; it never creates persona facts.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Protocol, Sequence
from urllib.parse import quote_plus, urlsplit, urlunsplit

from langchain_mcp_adapters.client import MultiServerMCPClient

from api.services.company_url import normalize_url
from api.services.info_collection.url_tools import _build_worker_chrome_config
from api.services.url_security import assert_public_http_url
from browser_manager.provider import get_browser_provider
from core.logger import get_logger
from Sere1nGraph.graph.tools.mcp import build_mcp_connections


logger = get_logger("persona_research_browser")

SEARCH_QUERY_LIMIT = 4
SEARCH_RESULTS_PER_QUERY = 24
TARGET_READABLE_PAGES = 12
MIN_READABLE_PAGES = 8
MAX_CANDIDATE_ATTEMPTS = 24
MAX_PAGES_PER_HOST = 2
MCP_CALL_TIMEOUT_SECONDS = 24
NAVIGATION_TIMEOUT_MS = 12_000
PAGE_TEXT_LIMIT = 6_000

_SEARCH_HOST_SUFFIXES = ("bing.com", "bing.cn")
_SKIPPED_PATH_SUFFIXES = (
    ".7z",
    ".apk",
    ".dmg",
    ".doc",
    ".docx",
    ".exe",
    ".gif",
    ".jpg",
    ".jpeg",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".rar",
    ".svg",
    ".tar",
    ".wav",
    ".xls",
    ".xlsx",
    ".zip",
)

_SEARCH_RESULT_SCRIPT = """() => {
  const selectors = [
    '#b_results li.b_algo h2 a[href]',
    '#b_results h2 a[href]',
    'main h2 a[href]',
    'main h3 a[href]'
  ];
  const anchors = Array.from(document.querySelectorAll(selectors.join(',')));
  const seen = new Set();
  const items = [];
  for (const anchor of anchors) {
    const url = String(anchor.href || '').trim();
    const title = String(anchor.innerText || anchor.textContent || '').trim();
    if (!url.startsWith('http') || !title || seen.has(url)) continue;
    seen.add(url);
    const container = anchor.closest('li, article, section, div');
    const snippet = String(container?.innerText || '').replace(/\\s+/g, ' ').trim();
    items.push({url, title, snippet: snippet.slice(0, 500)});
    if (items.length >= 24) break;
  }
  return {url: location.href, title: document.title, items};
}"""

_LOCATION_SCRIPT = """() => ({url: location.href})"""

_PAGE_EXTRACTION_SCRIPT = f"""() => {{
  const candidates = Array.from(document.querySelectorAll(
    'article, main, [role="main"], .article, .article-content, .content'
  ));
  const texts = candidates
    .map(node => String(node.innerText || '').replace(/\\s+/g, ' ').trim())
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
  const fallback = String(document.body?.innerText || '')
    .replace(/\\s+/g, ' ')
    .trim();
  const canonical = document.querySelector('link[rel="canonical"]')?.href || location.href;
  const publisher = document.querySelector('meta[property="og:site_name"]')?.content
    || document.querySelector('meta[name="application-name"]')?.content
    || location.hostname;
  const description = document.querySelector('meta[name="description"]')?.content
    || document.querySelector('meta[property="og:description"]')?.content
    || '';
  return {{
    url: location.href,
    canonical_url: canonical,
    title: document.title,
    publisher: String(publisher).trim(),
    description: String(description).replace(/\\s+/g, ' ').trim().slice(0, 600),
    text: String(texts[0] || fallback).slice(0, {PAGE_TEXT_LIMIT})
  }};
}}"""


@dataclass(slots=True)
class ResearchCandidate:
    url: str
    title: str
    snippet: str
    query: str


@dataclass(slots=True)
class ResearchPage:
    url: str
    title: str
    publisher: str
    description: str
    text: str
    query: str

    def model_payload(self) -> dict[str, str]:
        return asdict(self)


class PersonaResearchBrowser(Protocol):
    async def collect(
        self,
        app_config: Any,
        *,
        search_queries: Sequence[str],
        task_id: str,
        research_key: str,
        excluded_urls: Sequence[str] | None = None,
        candidate_offset: int = 0,
    ) -> list[ResearchPage]: ...


def _host_matches(host: str, suffixes: Sequence[str]) -> bool:
    normalized = host.lower().rstrip(".")
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in suffixes
    )


def research_url_identity(url: str) -> str:
    normalized = normalize_url(url)
    if not normalized:
        return ""
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return ""
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port and port != (443 if parsed.scheme.lower() == "https" else 80):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def _candidate_url_allowed(url: str) -> bool:
    normalized = normalize_url(url)
    if not normalized:
        return False
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return False
    if parsed.username is not None or parsed.password is not None or not parsed.hostname:
        return False
    return not parsed.path.lower().endswith(_SKIPPED_PATH_SUFFIXES)


def _mcp_result_text(result: Any) -> str:
    blocks = getattr(result, "content", None) or []
    values: list[str] = []
    for block in blocks:
        if isinstance(block, dict):
            text = block.get("text")
        else:
            text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            values.append(text.strip())
    return "\n".join(values)


def _extract_json_object(text: str) -> dict[str, Any]:
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.I | re.S)
    candidates = [*fenced, text]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for match in re.finditer(r"\{", candidate):
            try:
                value, _ = decoder.raw_decode(candidate[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise ValueError("Chrome MCP 返回中没有 JSON 对象")


async def _call_mcp(session: Any, name: str, arguments: dict[str, Any]) -> str:
    result = await asyncio.wait_for(
        session.call_tool(name, arguments),
        timeout=MCP_CALL_TIMEOUT_SECONDS,
    )
    if bool(getattr(result, "isError", False)):
        raise RuntimeError(f"Chrome MCP {name} 调用失败：{_mcp_result_text(result)}")
    return _mcp_result_text(result)


def _round_robin_candidates(
    buckets: Sequence[Sequence[ResearchCandidate]],
    *,
    offset: int,
) -> list[ResearchCandidate]:
    rotated: list[list[ResearchCandidate]] = []
    for bucket in buckets:
        values = list(bucket)
        if values:
            shift = max(0, int(offset)) % len(values)
            values = values[shift:] + values[:shift]
        rotated.append(values)

    ordered: list[ResearchCandidate] = []
    for index in range(max((len(bucket) for bucket in rotated), default=0)):
        for bucket in rotated:
            if index < len(bucket):
                ordered.append(bucket[index])
    return ordered


class ChromeDevtoolsPersonaResearchBrowser:
    """Chrome MCP adapter with fixed search and read-only extraction stages."""

    async def _discover(
        self,
        session: Any,
        queries: Sequence[str],
    ) -> list[list[ResearchCandidate]]:
        buckets: list[list[ResearchCandidate]] = []
        for query in queries:
            search_url = (
                "https://cn.bing.com/search?count=20&setlang=zh-hans&q="
                + quote_plus(query)
            )
            await _call_mcp(
                session,
                "navigate_page",
                {"type": "url", "url": search_url, "timeout": NAVIGATION_TIMEOUT_MS},
            )
            payload = _extract_json_object(
                await _call_mcp(
                    session,
                    "evaluate_script",
                    {"function": _SEARCH_RESULT_SCRIPT},
                )
            )
            candidates: list[ResearchCandidate] = []
            seen: set[str] = set()
            for item in list(payload.get("items") or [])[:SEARCH_RESULTS_PER_QUERY]:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                identity = research_url_identity(url)
                if not identity or identity in seen or not _candidate_url_allowed(url):
                    continue
                seen.add(identity)
                candidates.append(
                    ResearchCandidate(
                        url=url,
                        title=str(item.get("title") or "").strip(),
                        snippet=str(item.get("snippet") or "").strip()[:500],
                        query=query,
                    )
                )
            buckets.append(candidates)
        return buckets

    async def _read_page(
        self,
        session: Any,
        candidate: ResearchCandidate,
    ) -> ResearchPage:
        await asyncio.wait_for(assert_public_http_url(candidate.url), timeout=6)
        await _call_mcp(
            session,
            "navigate_page",
            {
                "type": "url",
                "url": candidate.url,
                "timeout": NAVIGATION_TIMEOUT_MS,
            },
        )
        location_payload = _extract_json_object(
            await _call_mcp(
                session,
                "evaluate_script",
                {"function": _LOCATION_SCRIPT},
            )
        )
        actual_url = str(location_payload.get("url") or "").strip()
        await asyncio.wait_for(assert_public_http_url(actual_url), timeout=6)
        payload = _extract_json_object(
            await _call_mcp(
                session,
                "evaluate_script",
                {"function": _PAGE_EXTRACTION_SCRIPT},
            )
        )
        final_url = str(payload.get("url") or actual_url).strip()
        await asyncio.wait_for(assert_public_http_url(final_url), timeout=6)
        title = str(payload.get("title") or candidate.title).strip()
        text = str(payload.get("text") or "").strip()
        if len(text) < 600 or not title:
            raise ValueError("页面正文不足或缺少标题")
        return ResearchPage(
            url=final_url,
            title=title[:300],
            publisher=str(payload.get("publisher") or "").strip()[:160],
            description=str(payload.get("description") or "").strip()[:600],
            text=text[:PAGE_TEXT_LIMIT],
            query=candidate.query,
        )

    async def collect(
        self,
        app_config: Any,
        *,
        search_queries: Sequence[str],
        task_id: str,
        research_key: str,
        excluded_urls: Sequence[str] | None = None,
        candidate_offset: int = 0,
    ) -> list[ResearchPage]:
        queries = list(
            dict.fromkeys(
                str(query).strip() for query in search_queries if str(query).strip()
            )
        )[:SEARCH_QUERY_LIMIT]
        if not queries:
            raise ValueError("人设研究任务没有可执行的公网检索词")

        provider = get_browser_provider()
        lease_id = "persona_evidence_" + (research_key or task_id)
        cdp_url = await provider.get_cdp_endpoint(
            task_id=lease_id,
            purpose="persona_research",
        )
        if not cdp_url:
            raise RuntimeError("无法获取 Chrome 容器进行人设背景研究")

        excluded = {
            identity
            for value in excluded_urls or []
            if (identity := research_url_identity(str(value or "")))
        }
        try:
            worker_config = _build_worker_chrome_config(app_config, cdp_url)
            connections = build_mcp_connections(
                worker_config,
                server_names="chrome-devtools",
            )
            client = MultiServerMCPClient(connections)
            async with client.session("chrome-devtools") as session:
                buckets = await self._discover(session, queries)
                candidates = _round_robin_candidates(
                    buckets,
                    offset=candidate_offset,
                )
                pages: list[ResearchPage] = []
                seen_urls = set(excluded)
                host_counts: dict[str, int] = {}
                attempts = 0
                for candidate in candidates:
                    if attempts >= MAX_CANDIDATE_ATTEMPTS:
                        break
                    candidate_identity = research_url_identity(candidate.url)
                    if not candidate_identity or candidate_identity in seen_urls:
                        continue
                    attempts += 1
                    try:
                        page = await self._read_page(session, candidate)
                        identity = research_url_identity(page.url)
                        host = (urlsplit(page.url).hostname or "").lower().rstrip(".")
                        if (
                            not identity
                            or identity in seen_urls
                            or not host
                            or _host_matches(host, _SEARCH_HOST_SUFFIXES)
                            or host_counts.get(host, 0) >= MAX_PAGES_PER_HOST
                        ):
                            continue
                        seen_urls.add(identity)
                        host_counts[host] = host_counts.get(host, 0) + 1
                        pages.append(page)
                        if len(pages) >= TARGET_READABLE_PAGES:
                            break
                    except Exception as exc:  # noqa: BLE001
                        logger.info(
                            "[persona_research_browser] task=%s candidate=%s skipped: %s",
                            task_id,
                            candidate.url,
                            exc,
                        )
        finally:
            try:
                await provider.release_cdp_endpoint(lease_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[persona_research_browser] task=%s Chrome 租约释放失败",
                    task_id,
                )

        if len(pages) < MIN_READABLE_PAGES:
            raise RuntimeError(
                f"Chrome 只读取到 {len(pages)} 个有效公网来源，"
                f"少于要求的 {MIN_READABLE_PAGES} 个"
            )
        return pages


_BROWSER_FACTORIES: dict[str, Callable[[], PersonaResearchBrowser]] = {
    "chrome-devtools": ChromeDevtoolsPersonaResearchBrowser,
}


def register_persona_research_browser(
    name: str,
    factory: Callable[[], PersonaResearchBrowser],
) -> None:
    _BROWSER_FACTORIES[str(name).strip()] = factory


def create_persona_research_browser(
    name: str = "chrome-devtools",
) -> PersonaResearchBrowser:
    try:
        factory = _BROWSER_FACTORIES[name]
    except KeyError as exc:
        raise ValueError(f"未知人设研究浏览器 Provider：{name}") from exc
    return factory()
