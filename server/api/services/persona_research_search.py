"""Search source definitions and a cheap guard against unrelated result pages."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urlsplit


class SearchSourceBlocked(RuntimeError):
    """A search provider requires human verification before it can be used."""


@dataclass(frozen=True, slots=True)
class PersonaSearchSource:
    name: str
    url_prefix: str
    selectors: tuple[str, ...]
    original_url_attribute: str = ""
    result_page_paths: tuple[str, ...] = ()

    def search_url(self, query: str) -> str:
        return self.url_prefix + quote_plus(query)

    def validate_response(self, payload: dict) -> None:
        parsed = urlsplit(str(payload.get("url") or ""))
        location = (str(parsed.hostname or "") + parsed.path).casefold()
        title = str(payload.get("title") or "").strip().casefold()
        if re.search(r"(?:^|[./_-])(?:q?captcha|challenge)(?:[./_-]|$)", location) or title.startswith(
            ("访问异常", "安全验证", "人机验证", "verify you are human")
        ):
            raise SearchSourceBlocked(f"{self.name} 要求人工验证，本轮暂停该检索源")

    def extraction_script(self) -> str:
        return """() => {
          const selectors = SELECTORS;
          const attribute = ATTRIBUTE;
          const items = [], seen = new Set();
          for (const anchor of document.querySelectorAll(selectors.join(','))) {
            const original = attribute ? anchor.getAttribute(attribute) : '';
            const url = String(original || anchor.href || '').trim();
            const title = String(anchor.innerText || anchor.textContent || '').trim();
            if (!url.startsWith('http') || !title || seen.has(url)) continue;
            seen.add(url);
            const container = anchor.closest('li, article, section, div');
            const snippet = String(container?.innerText || container?.textContent || '')
              .replace(/\\s+/g, ' ').trim().slice(0, 500);
            items.push({url, title, snippet});
            if (items.length >= 24) break;
          }
          return {url: location.href, title: document.title, items};
        }""".replace("SELECTORS", json.dumps(self.selectors)).replace(
            "ATTRIBUTE", json.dumps(self.original_url_attribute)
        )


_SOURCES = {
    "bing": PersonaSearchSource(
        "bing", "https://cn.bing.com/search?count=20&setlang=zh-hans&q=",
        ("#b_results li.b_algo h2 a[href]",),
    ),
    "so360": PersonaSearchSource(
        "so360", "https://www.so.com/s?q=",
        ("#main .res-list h3 a[href]",), "data-mdurl", ("/i",),
    ),
}


def register_persona_search_source(source: PersonaSearchSource) -> None:
    _SOURCES[source.name] = source


def persona_search_sources() -> tuple[PersonaSearchSource, ...]:
    return tuple(_SOURCES.values())


def is_search_result_url(url: str) -> bool:
    parsed = urlsplit(url)
    for source in persona_search_sources():
        target = urlsplit(source.url_prefix)
        host = str(target.hostname or "").removeprefix("www.")
        related_host = parsed.hostname == host or str(parsed.hostname or "").endswith("." + host)
        if related_host and parsed.path in (target.path, *source.result_page_paths):
            return True
    return False


def candidate_matches_query(query: str, title: str, snippet: str) -> bool:
    """Reject obvious first-character and unrelated-product search degradation.

    This is a discovery guard, not semantic evidence verification. Chinese
    subject prefixes need two matching bigrams; Latin queries need two terms.
    """
    cleaned = re.sub(r"\b(?:site|filetype):\S+", "", query, flags=re.I)
    text = (title + " " + snippet).casefold()
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", cleaned)
    if chinese:
        subject = chinese[0][:4]
        if subject in text:
            return True
        grams = {subject[index:index + 2] for index in range(len(subject) - 1)}
        return len(subject) >= 4 and sum(gram in text for gram in grams) >= 2
    terms = set(re.findall(r"[a-zA-Z]{3,}", cleaned.casefold())) - {
        "the", "and", "for", "with", "from", "site", "http", "https",
    }
    return bool(terms) and sum(
        bool(re.search(r"\b" + re.escape(term) + r"\b", text)) for term in terms
    ) >= min(2, len(terms))
