"""Official portal link classification shared by discovery and research browsing."""
from __future__ import annotations
import re
from urllib.parse import urlsplit

FRIEND_MARKERS = re.compile(r"友情链接|友好链接|合作伙伴|友情连接|友链|friend(?:ship|ly)?[-_ ]?links?|yqlj|partner[-_ ]?links?", re.I)
CHILD_MARKERS = re.compile(r"下属单位|直属单位|所属单位|下辖单位|直属机构|子公司|成员单位|成员企业|下属机构")
PARENT_MARKERS = re.compile(r"主管单位|上级单位|直接主管|上级机构")

def relation_hint(label: str, context: str = "") -> str:
    text = label + " " + context
    if FRIEND_MARKERS.search(text): return "friend"
    if CHILD_MARKERS.search(text): return "subordinate"
    if PARENT_MARKERS.search(text): return "parent"
    return "ordinary"

def html_link_context(node) -> str:
    """Nearby section headings, never the entire page/ancestor body text."""
    values = []
    for parent in list(node.iterancestors())[:4]:
        if parent.tag in {"body", "html"}: break
        values.extend(str(parent.get(key) or "") for key in ("id", "class", "aria-label", "label"))
        for child in list(parent)[:3]:
            if child.tag in {"h1", "h2", "h3", "h4", "label", "legend", "strong", "dt"}:
                values.append(child.text_content()[:100])
    return " ".join(values)[:600]

def canonical_link(url: str) -> str:
    try:
        parts = urlsplit(url)
        return parts._replace(fragment="").geturl() if parts.scheme in {"http", "https"} and parts.hostname else ""
    except ValueError: return ""

def in_roots(url: str, roots: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == root or host.endswith("." + root) for root in roots if root)
