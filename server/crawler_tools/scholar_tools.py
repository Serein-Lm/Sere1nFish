"""
学者学术联系发现 — 采集适配层

把「单位名 + 研究方向」解析为匹配文章，并抽取每篇文章绑定的、为学术通信而公开的
通讯作者邮箱。合并多个已实测可用的公开学术数据源：

  - OpenAlex     机构解析(支持中文名) + 论文检索 + is_corresponding 作者
  - ORCID        通讯作者本人主动公开的邮箱(仅本人公开时才有)
  - PubMed       E-utilities esearch/efetch，医学最全，Affiliation 内邮箱
  - Europe PMC   开放全文 <corresp> 明文通讯邮箱(姓名->邮箱精确绑定)
  - DOAJ         开放获取期刊题录(DOI)
  - CrossRef     题录/DOI 覆盖
  - OpenAIRE     欧盟聚合，摘要/版权文本含邮箱

设计意图(契合 AGENTS.md 统一适配层)：
    业务/流水线只调 discover() 拿聚合结果、normalize_to_docs() 拿可入库实体，
    各源的 HTTP 细节收敛在本模块，不外泄到 service/router。

合规边界(写进语义)：
    只登记按「文章」绑定的、为学术通信而公开的通讯/联系邮箱；
    不聚合「单位->人员联系方式名单」，不采集个人电话。

依赖仅标准库；HTTP 代理从 HTTPS_PROXY/HTTP_PROXY 环境变量继承。
需要 key 的源(Semantic Scholar/CORE/Lens)本次仅占位，后期经 api.dao.config 统一接入。
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

UA = "acad-collab-finder/1.0 (mailto:contact@example.com)"
logger = logging.getLogger(__name__)

_PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS = 5 * 60
_PROVIDER_CIRCUIT_LOCK = threading.Lock()
_PROVIDER_CIRCUIT_UNTIL: dict[str, float] = {}


class ScholarProviderTemporarilyUnavailable(RuntimeError):
    """One optional scholar provider is cooling down after rate limiting."""


def _provider_circuit_remaining(host: str) -> float:
    with _PROVIDER_CIRCUIT_LOCK:
        blocked_until = _PROVIDER_CIRCUIT_UNTIL.get(host, 0.0)
    return max(0.0, blocked_until - time.monotonic())


def _trip_provider_circuit(host: str, *, retry_after: float = 0.0) -> float:
    cooldown = max(_PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS, retry_after)
    with _PROVIDER_CIRCUIT_LOCK:
        _PROVIDER_CIRCUIT_UNTIL[host] = max(
            _PROVIDER_CIRCUIT_UNTIL.get(host, 0.0),
            time.monotonic() + cooldown,
        )
    return cooldown


def _clear_provider_circuit(host: str) -> None:
    with _PROVIDER_CIRCUIT_LOCK:
        _PROVIDER_CIRCUIT_UNTIL.pop(host, None)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# 噪声邮箱：出版社/编辑部/平台，非学者通信邮箱，入库前过滤。
_NOISE_LOCAL = {
    "permissions", "journalpermissions", "info", "support", "service",
    "editor", "editorial", "office", "admin", "webmaster", "contact",
    "help", "noreply", "no-reply",
}
_NOISE_DOMAIN_KEYS = (
    "sciengine.com", "mdpi.com/journal", "elsevier.com", "springer.com",
    "wiley.com", "plos.org", "example.",
)

# 多段 cn 顶级域放最前，避免 chenmy@sysucc.org.cn 被截成 .org。
_TLD_STOP = re.compile(
    r"^([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+?\.(?:edu\.cn|org\.cn|com\.cn|"
    r"gov\.cn|ac\.cn|net\.cn|com|org|net|edu|gov|cn|io|co|de|jp|uk|fr|au|ca))"
    r"(?![A-Za-z0-9])",
    re.I,
)


# ════════════════════════════════════════════════════════════
# HTTP 基础
# ════════════════════════════════════════════════════════════

def _get(url: str, headers: dict | None = None, retries: int = 4,
         timeout: int = 25) -> Any:
    """GET JSON with bounded provider-aware retries. Proxy comes from env."""
    hdr = {"User-Agent": UA, **(headers or {})}
    host = urllib.parse.urlsplit(url).hostname or "unknown"
    last: Exception | None = None
    for i in range(retries):
        circuit_remaining = _provider_circuit_remaining(host)
        if circuit_remaining > 0:
            raise ScholarProviderTemporarilyUnavailable(
                f"Scholar provider {host} cooling down for "
                f"{circuit_remaining:.0f}s after rate limiting"
            )
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                result = json.loads(r.read().decode("utf-8"))
            _clear_provider_circuit(host)
            return result
        except Exception as exc:  # noqa: BLE001
            last = exc
            retry_after = 0.0
            status = 0
            if isinstance(exc, urllib.error.HTTPError):
                status = int(exc.code or 0)
                try:
                    retry_after = float(exc.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    retry_after = 0.0
            if status == 429:
                cooldown = _trip_provider_circuit(
                    host,
                    retry_after=retry_after,
                )
                logger.warning(
                    "Scholar provider rate limited; circuit opened "
                    "host=%s cooldown=%.0fs",
                    host,
                    cooldown,
                )
                break
            if i + 1 >= retries:
                break
            delay = 1.2 * (i + 1)
            if status in {500, 502, 503, 504}:
                delay = max(retry_after, 2.0 ** (i + 1))
            delay = min(30.0, delay) + random.uniform(0.0, 0.4)
            logger.warning(
                "Scholar provider request failed; retrying host=%s status=%s "
                "attempt=%s/%s delay=%.1fs",
                host,
                status or type(exc).__name__,
                i + 1,
                retries,
                delay,
            )
            time.sleep(delay)
    raise last  # type: ignore[misc]


def _get_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _clean_emails(text: str) -> list[str]:
    found = sorted(set(EMAIL_RE.findall(text)))
    return [
        x for x in found
        if not x.lower().endswith((".png", ".jpg", ".gif", ".svg", ".css", ".js"))
    ]


def _clean_emails_in_order(text: str) -> list[str]:
    output: list[str] = []
    for value in EMAIL_RE.findall(text):
        if value.lower().endswith((".png", ".jpg", ".gif", ".svg", ".css", ".js")):
            continue
        if value not in output:
            output.append(value)
    return output


# ════════════════════════════════════════════════════════════
# 邮箱归一 / 去噪
# ════════════════════════════════════════════════════════════

def is_noise_email(email: str) -> bool:
    e = email.lower().strip(" .;,")
    local = e.split("@")[0]
    if local in _NOISE_LOCAL:
        return True
    return any(k in e for k in _NOISE_DOMAIN_KEYS)


def normalize_email(email: str) -> str:
    e = email.strip(" .;,<>()[]").lower()
    m = _TLD_STOP.match(e)
    return m.group(1) if m else e


# ════════════════════════════════════════════════════════════
# 结构化实体
# ════════════════════════════════════════════════════════════

@dataclass
class Source:
    source_key: str
    name: str
    access: str    # api / agent / hybrid
    region: str    # cn / global
    status: str    # verified / probe / excluded


@dataclass
class Article:
    article_id: str
    title: str
    year: Optional[str] = None
    doi: Optional[str] = None
    pmcid: Optional[str] = None
    unit: Optional[str] = None
    direction: Optional[str] = None
    source_keys: list[str] = field(default_factory=list)
    landing_page: Optional[str] = None
    unit_verified: bool = False
    match_evidence: str = ""


@dataclass
class Contact:
    email: str
    article_id: str
    source_key: str
    author_name: Optional[str] = None
    is_corresponding: bool = False
    unit: Optional[str] = None
    unit_verified: bool = False
    verification_authoritative: bool = False
    evidence: str = ""
    email_kind: str = ""


# ════════════════════════════════════════════════════════════
# API 源: OpenAlex + ORCID
# ════════════════════════════════════════════════════════════

def _resolve_institution(unit: str) -> list[dict] | None:
    """单位名(支持中文) -> OpenAlex 机构候选列表。"""
    q = urllib.parse.quote(unit)
    d = _get(f"https://api.openalex.org/institutions?search={q}&per-page=5")
    res = d.get("results", [])
    if not res:
        return None
    candidates: list[dict[str, Any]] = []
    for item in res:
        names = [
            item.get("display_name"),
            *(item.get("display_name_alternatives") or []),
            *(item.get("display_name_acronyms") or []),
        ]
        international = item.get("international") or {}
        if isinstance(international, dict):
            names.extend(international.values())
        aliases = list(dict.fromkeys(str(name).strip() for name in names if str(name or "").strip()))
        candidates.append({
            "id": item["id"].rsplit("/", 1)[-1],
            "name": item["display_name"],
            "aliases": aliases,
            "country": item.get("country_code"),
            "ror": item.get("ror"),
            "works": item.get("works_count", 0),
        })
    return candidates


_ORG_CN_IDENTITY_NOISE = re.compile(
    r"(?:股份有限责任公司|股份有限公司|有限责任公司|有限公司|集团)"
)
_ORG_EN_IDENTITY_NOISE = re.compile(
    r"\b(?:the|company|corporation|corp|co|ltd|limited|inc)\b",
    re.I,
)


def _organization_identity(value: str) -> str:
    text = _ORG_CN_IDENTITY_NOISE.sub("", str(value or "").casefold())
    text = _ORG_EN_IDENTITY_NOISE.sub("", text)
    return re.sub(
        r"[^a-z0-9\u3400-\u9fff]+",
        "",
        text,
    )


def _organization_full_identity(value: str) -> str:
    return re.sub(
        r"[^a-z0-9\u3400-\u9fff]+",
        "",
        str(value or "").casefold(),
    )


def _text_matches_organization_alias(text: str, alias: str) -> bool:
    """Match an organization name without treating an email domain as evidence."""
    observed_text = EMAIL_RE.sub(" ", str(text or ""))
    observed_text = re.sub(r"https?://\S+", " ", observed_text, flags=re.I)
    expected_text = str(alias or "").strip().casefold()
    if not expected_text:
        return False

    # Short ASCII identifiers are ambiguous. They must occur as a standalone
    # affiliation token, e.g. ``(CICT)``, rather than inside ``ccrc.uga.edu``.
    if re.fullmatch(r"[a-z0-9]{2,10}", expected_text, flags=re.I):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(expected_text)}(?![a-z0-9])",
                observed_text.casefold(),
            )
        )

    expected = _organization_identity(expected_text)
    observed = _organization_identity(observed_text)
    return len(expected) >= 4 and expected in observed


def _institution_matches(
    unit: str,
    candidate_name: str,
    unit_en: str = "",
) -> bool:
    candidate_full = _organization_full_identity(candidate_name)
    for expected_name in (unit, unit_en):
        expected_full = _organization_full_identity(expected_name)
        if expected_full and expected_full == candidate_full:
            return True

    candidate = _organization_identity(candidate_name)
    if len(candidate) < 4:
        return False
    for expected_name in (unit, unit_en):
        expected = _organization_identity(expected_name)
        if len(expected) < 4:
            continue
        shorter, longer = sorted((expected, candidate), key=len)
        if shorter in longer and len(shorter) / len(longer) >= 0.6:
            return True
    return False


def _institution_candidate_matches(
    unit: str,
    candidate: dict[str, Any],
    unit_en: str = "",
) -> bool:
    names = candidate.get("aliases") or [candidate.get("name", "")]
    return any(_institution_matches(unit, str(name), unit_en) for name in names)


def _orcid_public_email(orcid_url: str | None) -> list[str]:
    """取通讯作者本人主动公开的邮箱(仅本人公开时才有)。"""
    if not orcid_url:
        return []
    oid = orcid_url.rsplit("/", 1)[-1]
    try:
        d = _get(
            f"https://pub.orcid.org/v3.0/{oid}/record",
            headers={"Accept": "application/json"},
        )
        return [
            e.get("email")
            for e in d.get("person", {}).get("emails", {}).get("email", [])
            if e.get("email")
        ]
    except Exception:  # noqa: BLE001
        return []


def _openalex_articles(unit: str, direction: str, limit: int,
                       unit_en: str,
                       enrich_orcid_email: bool) -> dict:
    cands = _resolve_institution(unit)
    if not cands:
        return {"error": f"未解析到单位: {unit}", "articles": []}
    inst = next(
        (
            candidate
            for candidate in cands
            if _institution_candidate_matches(unit, candidate, unit_en)
        ),
        None,
    )
    if not inst:
        return {
            "error": f"OpenAlex 机构候选与目标单位不一致: {unit}",
            "unit": None,
            "institution_candidates": cands,
            "institution_verified": False,
            "articles": [],
        }
    inst_id = inst["id"]

    q = urllib.parse.quote(direction)
    url = (
        f"https://api.openalex.org/works?"
        f"filter=authorships.institutions.id:{inst_id}&search={q}"
        f"&sort=cited_by_count:desc&per-page={limit}"
        f"&select=title,doi,publication_year,authorships,primary_location,cited_by_count"
    )
    d = _get(url)
    articles = []
    for w in d.get("results", []):
        corr = []
        for a in w.get("authorships", []):
            if not a.get("is_corresponding"):
                continue
            au = a.get("author", {})
            emails = (
                _orcid_public_email(au.get("orcid")) if enrich_orcid_email else []
            )
            corr.append({
                "name": au.get("display_name"),
                "orcid": au.get("orcid"),
                "public_emails": emails,
            })
        articles.append({
            "title": w.get("title"),
            "doi": w.get("doi"),
            "year": w.get("publication_year"),
            "cited_by": w.get("cited_by_count"),
            "landing_page": (w.get("primary_location") or {}).get("landing_page_url"),
            "corresponding": corr,
        })
    return {
        "unit": inst,
        "institution_verified": True,
        "match_evidence": f"OpenAlex institution={inst['name']}",
        "institution_candidates": cands,
        "direction": direction,
        "count": d.get("meta", {}).get("count"),
        "articles": articles,
    }


# ════════════════════════════════════════════════════════════
# 邮箱抽取源: PubMed / EuropePMC / DOAJ / CrossRef / OpenAIRE
# ════════════════════════════════════════════════════════════

def _element_text(node: ET.Element | None) -> str:
    return "" if node is None else re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def _affiliation_matches_unit(affiliation: str, unit: str) -> bool:
    return _text_matches_organization_alias(affiliation, unit)


def _parse_pubmed_articles(xml: str, *, unit: str) -> list[dict[str, Any]]:
    """Bind each public email to its PubMed article and author evidence."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []

    output: list[dict[str, Any]] = []
    for entry in root.findall(".//PubmedArticle"):
        citation = entry.find("./MedlineCitation")
        article = citation.find("./Article") if citation is not None else None
        if citation is None or article is None:
            continue
        pmid = str(citation.findtext("./PMID") or "").strip()
        title = _element_text(article.find("./ArticleTitle"))
        doi = ""
        for identifier in entry.findall("./PubmedData/ArticleIdList/ArticleId"):
            if str(identifier.attrib.get("IdType") or "").casefold() == "doi":
                doi = _element_text(identifier)
                break
        year = str(article.findtext("./Journal/JournalIssue/PubDate/Year") or "").strip()
        if not year:
            medline_date = str(
                article.findtext("./Journal/JournalIssue/PubDate/MedlineDate") or ""
            )
            match = re.search(r"\b(?:19|20)\d{2}\b", medline_date)
            year = match.group(0) if match else ""

        contacts: dict[str, dict[str, Any]] = {}
        article_verified = False
        article_evidence = ""
        for author in article.findall("./AuthorList/Author"):
            author_name = " ".join(
                value
                for value in (
                    str(author.findtext("./ForeName") or "").strip(),
                    str(author.findtext("./LastName") or "").strip(),
                )
                if value
            ) or str(author.findtext("./CollectiveName") or "").strip()
            for affiliation_node in author.findall("./AffiliationInfo/Affiliation"):
                affiliation = _element_text(affiliation_node)
                verified = _affiliation_matches_unit(affiliation, unit)
                if verified:
                    article_verified = True
                    article_evidence = article_evidence or affiliation[:240]
                corresponding = "electronic address" in affiliation.casefold()
                for raw_email in _clean_emails(affiliation):
                    email = normalize_email(raw_email)
                    if not email or is_noise_email(email):
                        continue
                    existing = contacts.get(email)
                    candidate = {
                        "email": email,
                        "author_name": author_name or None,
                        "is_corresponding": corresponding,
                        "unit_verified": verified,
                        "verification_authoritative": True,
                        "evidence": affiliation[:240],
                        "email_kind": _email_kind(email),
                    }
                    if existing is None or (
                        candidate["unit_verified"], candidate["is_corresponding"]
                    ) > (
                        existing["unit_verified"], existing["is_corresponding"]
                    ):
                        contacts[email] = candidate

        output.append(
            {
                "pmid": pmid,
                "title": title,
                "doi": doi or None,
                "year": year,
                "landing_page": (
                    f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""
                ),
                "unit_verified": article_verified,
                "match_evidence": article_evidence,
                "contacts": list(contacts.values()),
            }
        )
    return output


def _local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1]


def _jats_element_id(node: ET.Element) -> str:
    return str(
        node.attrib.get("id")
        or node.attrib.get("{http://www.w3.org/XML/1998/namespace}id")
        or ""
    ).strip()


def _jats_descendants(node: ET.Element, name: str) -> list[ET.Element]:
    return [item for item in node.iter() if _local_name(item.tag) == name]


def _jats_text(node: ET.Element) -> str:
    return re.sub(r"\s+", " ", " ".join(node.itertext())).strip()


def _jats_xref_ids(node: ET.Element, ref_type: str) -> list[str]:
    output: list[str] = []
    for xref in _jats_descendants(node, "xref"):
        if str(xref.attrib.get("ref-type") or "").casefold() != ref_type.casefold():
            continue
        output.extend(
            part for part in re.split(r"[\s,;]+", str(xref.attrib.get("rid") or ""))
            if part
        )
    return list(dict.fromkeys(output))


def _jats_author_name(contrib: ET.Element) -> str:
    surname = next(
        (_element_text(item) for item in _jats_descendants(contrib, "surname") if _element_text(item)),
        "",
    )
    given = next(
        (_element_text(item) for item in _jats_descendants(contrib, "given-names") if _element_text(item)),
        "",
    )
    if surname or given:
        return " ".join(value for value in (given, surname) if value)
    return next(
        (
            _element_text(item)
            for item in _jats_descendants(contrib, "collab")
            if _element_text(item)
        ),
        "",
    )


def _verify_bound_evidence(evidence_blocks: list[str], unit: str) -> tuple[bool, str]:
    """Verify only evidence explicitly linked to one author/contact."""
    positive, negative = _unit_aliases(unit)
    cleaned = [re.sub(r"\s+", " ", value).strip() for value in evidence_blocks if value]
    for value in cleaned:
        if any(_text_matches_organization_alias(value, alias) for alias in negative):
            return False, "NEG:" + value[:200]
    for value in cleaned:
        if any(_text_matches_organization_alias(value, alias) for alias in positive):
            return True, value[:240]
    return False, (cleaned[0][:240] if cleaned else "")


def _parse_europepmc_full_text(xml: str, *, unit: str) -> dict[str, Any]:
    """Parse JATS XML with exact author -> affiliation -> email bindings.

    Article-wide affiliations are valid article evidence, but are never used to
    verify an unrelated author's email. Unbound emails are returned as an
    authoritative negative so a later rerun can correct legacy false positives.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {
            "emails": [],
            "contacts": [],
            "corresp": [],
            "affs": [],
            "unit_verified": False,
            "match_evidence": "",
        }

    affiliation_by_id: dict[str, str] = {}
    affiliation_blocks: list[str] = []
    for node in _jats_descendants(root, "aff"):
        value = _jats_text(node)
        if not value:
            continue
        affiliation_blocks.append(value[:400])
        node_id = _jats_element_id(node)
        if node_id:
            affiliation_by_id[node_id] = value

    correspondence_by_id: dict[str, dict[str, Any]] = {}
    correspondence_entries: list[tuple[str, dict[str, Any]]] = []
    correspondence_blocks: list[str] = []
    for node in _jats_descendants(root, "corresp"):
        text = _jats_text(node)
        emails = [
            normalize_email(value)
            for value in _clean_emails_in_order(text)
            if normalize_email(value) and not is_noise_email(normalize_email(value))
        ]
        block = {"text": text, "emails": list(dict.fromkeys(emails))}
        correspondence_blocks.append(text[:400])
        node_id = _jats_element_id(node)
        correspondence_entries.append((node_id, block))
        if node_id:
            correspondence_by_id[node_id] = block

    contacts: dict[str, dict[str, Any]] = {}

    def register_contact(
        email: str,
        *,
        author_name: str = "",
        corresponding: bool = False,
        evidence_blocks: list[str] | None = None,
    ) -> None:
        normalized = normalize_email(email)
        if not normalized or is_noise_email(normalized):
            return
        verified, evidence = _verify_bound_evidence(evidence_blocks or [], unit)
        candidate = {
            "email": normalized,
            "author_name": author_name or None,
            "is_corresponding": bool(corresponding),
            "unit_verified": verified,
            "verification_authoritative": True,
            "evidence": evidence,
            "email_kind": _email_kind(normalized),
        }
        existing = contacts.get(normalized)
        if existing is None or (
            candidate["unit_verified"],
            candidate["is_corresponding"],
            bool(candidate["author_name"]),
        ) > (
            existing["unit_verified"],
            existing["is_corresponding"],
            bool(existing["author_name"]),
        ):
            contacts[normalized] = candidate

    author_contribs = [
        contrib
        for contrib in _jats_descendants(root, "contrib")
        if str(contrib.attrib.get("contrib-type") or "author").casefold()
        in {"", "author"}
    ]
    correspondence_authors: dict[str, list[ET.Element]] = {}
    for contrib in author_contribs:
        for corresp_id in _jats_xref_ids(contrib, "corresp"):
            correspondence_authors.setdefault(corresp_id, []).append(contrib)

    referenced_correspondence_ids: set[str] = set()
    for contrib in author_contribs:
        contrib_type = str(contrib.attrib.get("contrib-type") or "author").casefold()
        if contrib_type not in {"", "author"}:
            continue
        author_name = _jats_author_name(contrib)
        affiliation_ids = _jats_xref_ids(contrib, "aff")
        author_affiliations = [
            affiliation_by_id[aff_id]
            for aff_id in affiliation_ids
            if aff_id in affiliation_by_id
        ]
        for embedded_affiliation in _jats_descendants(contrib, "aff"):
            value = _jats_text(embedded_affiliation)
            if value and value not in author_affiliations:
                author_affiliations.append(value)

        corresp_ids = _jats_xref_ids(contrib, "corresp")
        referenced_correspondence_ids.update(corresp_ids)
        corresp_blocks = [
            correspondence_by_id[corresp_id]
            for corresp_id in corresp_ids
            if corresp_id in correspondence_by_id
        ]
        direct_emails = {
            normalize_email(raw_email)
            for email_node in _jats_descendants(contrib, "email")
            for raw_email in _clean_emails(_jats_text(email_node))
        }
        linked_emails = set(direct_emails)
        if not linked_emails:
            for corresp_id in corresp_ids:
                block = correspondence_by_id.get(corresp_id) or {}
                block_emails = list(block.get("emails") or [])
                referencing_authors = correspondence_authors.get(corresp_id) or []
                if len(block_emails) == 1:
                    linked_emails.add(block_emails[0])
                elif len(block_emails) == len(referencing_authors):
                    try:
                        linked_emails.add(
                            block_emails[referencing_authors.index(contrib)]
                        )
                    except (IndexError, ValueError):
                        pass
        linked_evidence = [*author_affiliations] or [
            str(block.get("text") or "") for block in corresp_blocks
        ]
        is_corresponding = (
            str(contrib.attrib.get("corresp") or "").casefold() in {"yes", "true", "1"}
            or bool(corresp_blocks)
            or bool(direct_emails)
        )
        for email in linked_emails:
            register_contact(
                email,
                author_name=author_name,
                corresponding=is_corresponding,
                evidence_blocks=linked_evidence,
            )

    for corresp_id, block in correspondence_entries:
        if corresp_id in referenced_correspondence_ids:
            continue
        block_emails = list(block.get("emails") or [])
        for email in block_emails:
            register_contact(
                email,
                corresponding=True,
                evidence_blocks=(
                    [str(block.get("text") or "")]
                    if len(block_emails) == 1
                    else []
                ),
            )

    all_emails = {
        normalize_email(raw_email)
        for raw_email in _clean_emails(xml)
    }
    for email in all_emails:
        if email and email not in contacts:
            register_contact(email, evidence_blocks=[])

    article_verified, article_evidence = _verify_bound_evidence(
        affiliation_blocks,
        unit,
    )
    return {
        "emails": sorted(contacts),
        "contacts": list(contacts.values()),
        "corresp": correspondence_blocks,
        "affs": affiliation_blocks,
        "unit_verified": article_verified,
        "match_evidence": article_evidence,
    }


def _pubmed(unit: str, direction: str, retmax: int = 8) -> dict:
    term = f"{unit}[AFFL] AND {direction}"
    u = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
         f"db=pubmed&term={urllib.parse.quote(term)}&retmax={retmax}&retmode=json")
    d = _get(u)
    ids = d["esearchresult"]["idlist"]
    count = d["esearchresult"]["count"]
    emails: list[str] = []
    corresp: list[str] = []
    articles: list[dict[str, Any]] = []
    if ids:
        u2 = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
              f"db=pubmed&id={','.join(ids)}&retmode=xml")
        xml = _get_text(u2)
        articles = _parse_pubmed_articles(xml, unit=unit)
        emails = sorted(
            {
                contact["email"]
                for article in articles
                for contact in article.get("contacts") or []
            }
        )
        corresp = sorted(
            {
                contact["email"]
                for article in articles
                for contact in article.get("contacts") or []
                if contact.get("is_corresponding")
            }
        )
    return {"source": "pubmed", "hit_count": count, "pmids": ids,
            "emails": emails, "electronic_address": corresp,
            "articles": articles}


def _europepmc(unit: str, direction: str, page_size: int = 8) -> dict:
    q = f'AFF:"{unit}" AND "{direction}" AND OPEN_ACCESS:y'
    u = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
         f"query={urllib.parse.quote(q)}&format=json&pageSize={page_size}&resultType=core")
    d = _get(u)
    arts = []
    for r in d.get("resultList", {}).get("result", []):
        pmcid = r.get("pmcid")
        item: dict[str, Any] = {
            "title": r.get("title", "")[:200], "pmcid": pmcid,
            "doi": r.get("doi"), "year": r.get("pubYear"),
        }
        if pmcid:
            try:
                xml = _get_text(
                    f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML")
                item.update(_parse_europepmc_full_text(xml, unit=unit))
            except Exception:  # noqa: BLE001
                item["emails"] = []
        arts.append(item)
    return {"source": "europepmc", "hit_count": d.get("hitCount"), "articles": arts}


def _doaj(unit: str, direction: str, page_size: int = 6) -> dict:
    q = f'{direction} AND "{unit}"'
    u = f"https://doaj.org/api/v2/search/articles/{urllib.parse.quote(q)}?pageSize={page_size}"
    d = _get(u)
    arts = []
    for r in d.get("results", []):
        b = r.get("bibjson", {})
        doi = next(
            (i["id"] for i in b.get("identifier", []) if i.get("type") == "doi"),
            None,
        )
        arts.append({"title": b.get("title", "")[:200], "doi": doi})
    return {"source": "doaj", "total": d.get("total"), "articles": arts}


def _crossref(unit: str, direction: str, rows: int = 8) -> dict:
    u = ("https://api.crossref.org/works?"
         f"query.affiliation={urllib.parse.quote(unit)}"
         f"&query.bibliographic={urllib.parse.quote(direction)}"
         f"&rows={rows}&select=DOI,title,container-title,published,author")
    d = _get(u)
    arts = []
    for it in d.get("message", {}).get("items", []):
        arts.append({
            "title": (it.get("title") or [""])[0][:200],
            "doi": it.get("DOI"),
            "journal": (it.get("container-title") or [""])[0],
            "year": (it.get("published", {}).get("date-parts", [[None]])[0][0]),
        })
    return {"source": "crossref",
            "hit_count": d.get("message", {}).get("total-results"),
            "articles": arts}


def _openaire(unit: str, direction: str, size: int = 8) -> dict:
    u = ("https://api.openaire.eu/search/publications?"
         f"keywords={urllib.parse.quote(direction + ' ' + unit)}"
         f"&size={size}&format=json")
    raw = _get_text(u)
    emails = _clean_emails(raw)
    try:
        d = json.loads(raw)
        total = d.get("response", {}).get("header", {}).get("total", {}).get("$")
    except Exception:  # noqa: BLE001
        total = None
    return {"source": "openaire", "hit_count": total, "emails": emails}


def _extract_all(unit_en: str, direction_en: str) -> dict:
    """并联所有免 key 已验证源，返回按源分组的抽取结果。"""
    out: dict[str, Any] = {"unit": unit_en, "direction": direction_en, "sources": {}}
    for fn, key in [
        (_pubmed, "pubmed"), (_europepmc, "europepmc"), (_doaj, "doaj"),
        (_crossref, "crossref"), (_openaire, "openaire"),
    ]:
        try:
            out["sources"][key] = fn(unit_en, direction_en)
        except Exception as e:  # noqa: BLE001
            out["sources"][key] = {"error": str(e)}
    return out


# ════════════════════════════════════════════════════════════
# 统一入口: discover
# ════════════════════════════════════════════════════════════

def discover(unit: str, direction: str, unit_en: str = "", limit: int = 10,
             enrich_orcid_email: bool = True) -> dict:
    """
    统一入口: 一次调用合并所有数据源。

    Args:
        unit    : 机构名(中/英文皆可)，用于 OpenAlex 解析。
        direction: 研究方向关键词。
        unit_en : 英文机构名，用于 PubMed/EuropePMC/DOAJ 检索(默认回退 unit)。
        limit   : OpenAlex 返回文章数。
    """
    try:
        api_results = _openalex_articles(
            unit,
            direction,
            limit,
            unit_en or "",
            enrich_orcid_email,
        )
    except Exception as exc:  # noqa: BLE001
        api_results = {
            "error": str(exc),
            "articles": [],
            "institution_verified": False,
        }

    try:
        email_extraction = _extract_all(unit_en or unit, direction)
    except Exception as e:  # noqa: BLE001
        email_extraction = {"error": f"extractors 不可用: {e}"}

    return {
        "unit": unit,
        "unit_en": unit_en or unit,
        "direction": direction,
        "api_results": api_results,
        "email_extraction": email_extraction,
        "policy": "仅按文章绑定的公开学术联系渠道; 不导出整单位联系方式名单; 不取个人电话",
    }


# ════════════════════════════════════════════════════════════
# 机构级全量(无方向)分页深抓
# ════════════════════════════════════════════════════════════

def _europepmc_bulk(unit_en: str, max_articles: int = 2000,
                    page_size: int = 100, progress=None) -> dict:
    """
    机构级 EuropePMC 开放全文分页深抓(无方向)：
    按 AFF 检索开放获取文章，游标翻页，逐篇取全文 <corresp> 通讯邮箱。
    """
    q = f'AFF:"{unit_en}" AND OPEN_ACCESS:y'
    base = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    cursor = "*"
    arts: list[dict[str, Any]] = []
    hit_count = None
    fetched = 0
    while fetched < max_articles:
        page = min(page_size, max_articles - fetched)
        u = (f"{base}?query={urllib.parse.quote(q)}&format=json"
             f"&pageSize={page}&resultType=core&cursorMark={urllib.parse.quote(cursor)}")
        d = _get(u)
        if hit_count is None:
            hit_count = d.get("hitCount")
        results = d.get("resultList", {}).get("result", [])
        if not results:
            break
        for r in results:
            pmcid = r.get("pmcid")
            item: dict[str, Any] = {
                "title": r.get("title", "")[:200], "pmcid": pmcid,
                "doi": r.get("doi"), "year": r.get("pubYear"),
            }
            if pmcid:
                try:
                    xml = _get_text(
                        f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML")
                    item.update(_parse_europepmc_full_text(xml, unit=unit_en))
                except Exception:  # noqa: BLE001
                    item["emails"] = []
            arts.append(item)
        fetched += len(results)
        if progress:
            try:
                progress(fetched, hit_count)
            except Exception:  # noqa: BLE001
                pass
        next_cursor = d.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return {"source": "europepmc", "hit_count": hit_count, "articles": arts}


def europepmc_bulk_pages(unit_en: str, max_articles: int = 2000,
                         page_size: int = 100):
    """
    机构级 EuropePMC 分页深抓的「流式」版本：每翻一页 yield 一批，
    供调用侧逐批增量入库(前端可实时看到数据增长)，避免长跑到最后才落库。
    yield: {"articles": [...], "fetched": int, "hit_count": int}
    """
    q = f'AFF:"{unit_en}" AND OPEN_ACCESS:y'
    base = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    cursor = "*"
    hit_count = None
    fetched = 0
    while fetched < max_articles:
        page = min(page_size, max_articles - fetched)
        u = (f"{base}?query={urllib.parse.quote(q)}&format=json"
             f"&pageSize={page}&resultType=core&cursorMark={urllib.parse.quote(cursor)}")
        d = _get(u)
        if hit_count is None:
            hit_count = d.get("hitCount")
        results = d.get("resultList", {}).get("result", [])
        if not results:
            break
        batch: list[dict[str, Any]] = []
        page_errors: list[str] = []
        for r in results:
            pmcid = r.get("pmcid")
            item: dict[str, Any] = {
                "title": r.get("title", "")[:200], "pmcid": pmcid,
                "doi": r.get("doi"), "year": r.get("pubYear"),
            }
            if pmcid:
                try:
                    xml = _get_text(
                        f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML")
                    item.update(_parse_europepmc_full_text(xml, unit=unit_en))
                except Exception as exc:  # noqa: BLE001
                    item["emails"] = []
                    item["full_text_error"] = str(exc)[:500]
                    page_errors.append(f"{pmcid}: {exc}"[:500])
            batch.append(item)
        fetched += len(results)
        yield {
            "articles": batch,
            "fetched": fetched,
            "hit_count": hit_count,
            "errors": page_errors,
        }
        next_cursor = d.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor


SYSU_ALIASES = [
    "sun yat-sen university", "sun yat sen university", "sun yat-sen",
    "sysu", "sysucc", "sysush",
    "中山大学", "中山醫學院", "中山医学院",
    "zhongshan school of medicine",
    "cancer center of sun yat-sen",
    "cancer center, sun yat-sen",
]
SYSU_NEG_ALIASES = [
    "national sun yat-sen university", "nsysu",
    "国立中山大学", "國立中山大學",
    "sun yat-sen memorial",
]
UNIT_ALIAS_TABLE: dict[str, tuple[list[str], list[str]]] = {
    "中山大学": (SYSU_ALIASES, SYSU_NEG_ALIASES),
    "sun yat-sen university": (SYSU_ALIASES, SYSU_NEG_ALIASES),
}
DOMAIN_BLOCKLIST_BY_UNIT: dict[str, set[str]] = {
    "中山大学": {"mail.nsysu.edu", "mail.nsysu.edu.tw", "nsysu.edu.tw", "nsysu.edu"},
    "sun yat-sen university": {"mail.nsysu.edu", "mail.nsysu.edu.tw", "nsysu.edu.tw", "nsysu.edu"},
}


def _unit_aliases(unit: str) -> tuple[list[str], list[str]]:
    if not unit:
        return [], []
    key = unit.strip().lower()
    if key in UNIT_ALIAS_TABLE:
        return UNIT_ALIAS_TABLE[key]
    return [unit.strip().lower()], []


def _verify_person_unit(
    email: str,
    corresp_blocks: list[str],
    aff_blocks: list[str],
    unit: str,
) -> tuple[bool, str]:
    """Legacy fallback: verify only the correspondence block hosting email.

    Article-wide affiliations cannot prove that a particular email belongs to
    the target institution. ``aff_blocks`` remains in the signature for old
    normalized payload compatibility and is deliberately not used.
    """
    pos, neg = _unit_aliases(unit)
    if not pos:
        return False, ""
    em_lo = email.lower()
    # 1) 找到含该 email 的 corresp 段
    hosting = ""
    for c in corresp_blocks or []:
        if em_lo in c.lower():
            hosting = c
            break
    if hosting:
        if any(_text_matches_organization_alias(hosting, key) for key in neg):
            return False, "NEG:" + hosting[:120]
        if any(_text_matches_organization_alias(hosting, key) for key in pos):
            return True, hosting[:200]
        return False, hosting[:120]
    return False, ""


PERSONAL_EMAIL_DOMAINS = {
    "qq.com", "163.com", "126.com", "gmail.com", "outlook.com", "hotmail.com",
    "foxmail.com", "yahoo.com", "yahoo.com.cn", "sina.com", "sina.cn",
    "aliyun.com", "icloud.com", "live.com", "me.com", "139.com", "sohu.com",
    "vip.qq.com", "vip.163.com", "vip.126.com", "vip.sina.com",
}


def _email_kind(email: str) -> str:
    d = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    return "personal" if d in PERSONAL_EMAIL_DOMAINS else "institutional"


def normalize_bulk_batch(unit: str, articles: list[dict[str, Any]]):
    """把一批 EuropePMC 文章(europepmc_bulk_pages 的 articles)归一化为
    (article_docs, contact_docs)，并强制人物↔单位一致性验证：
    - 每个 email 必须通过 JATS 作者引用绑定到目标单位，或在其自身 <corresp> 段出现单位别名，
      否则 unit_verified=False；命中 NEG 别名(如 NSYSU/国立中山大学)直接标 False。
    - 已知别名冲突域名（如 mail.nsysu.edu*）直接丢弃，不入库。
    """
    art_docs: list[dict[str, Any]] = []
    con_docs: list[dict[str, Any]] = []
    seen_contacts: set[tuple[str, str]] = set()
    dom_block = DOMAIN_BLOCKLIST_BY_UNIT.get(unit.strip().lower(), set())
    for a in articles:
        aid = _article_id(a.get("doi"), a.get("pmcid"), a.get("title"))
        corresp_blocks = a.get("corresp", []) or []
        aff_blocks = a.get("affs", []) or []
        exact_contacts = a.get("contacts")
        article_verified = bool(a.get("unit_verified", False))
        article_evidence = str(a.get("match_evidence") or "")
        art_docs.append({
            "article_id": aid, "title": a.get("title", ""),
            "year": str(a.get("year") or ""), "doi": a.get("doi"),
            "pmcid": a.get("pmcid"), "unit": unit, "direction": "",
            "source_keys": ["europepmc"], "landing_page": None,
            "unit_verified": article_verified,
            "match_evidence": article_evidence,
        })
        if isinstance(exact_contacts, list):
            for contact in exact_contacts:
                e = normalize_email(contact.get("email") or "")
                if not e or is_noise_email(e) or (e, aid) in seen_contacts:
                    continue
                dom = e.rsplit("@", 1)[-1].lower()
                if dom in dom_block:
                    continue
                seen_contacts.add((e, aid))
                con_docs.append({
                    "email": e,
                    "article_id": aid,
                    "source_key": "europepmc",
                    "author_name": contact.get("author_name"),
                    "is_corresponding": bool(contact.get("is_corresponding")),
                    "unit": unit,
                    "unit_verified": bool(contact.get("unit_verified")),
                    "verification_authoritative": True,
                    "evidence": str(contact.get("evidence") or "")[:240],
                    "email_kind": contact.get("email_kind") or _email_kind(e),
                })
            continue
        pairs = _parse_corresp(corresp_blocks)
        bound = {normalize_email(e) for _, e in pairs}
        for name, em in pairs:
            e = normalize_email(em)
            if not e or is_noise_email(e) or (e, aid) in seen_contacts:
                continue
            dom = e.rsplit("@", 1)[-1].lower()
            if dom in dom_block:
                continue
            seen_contacts.add((e, aid))
            verified, evidence = _verify_person_unit(e, corresp_blocks, aff_blocks, unit)
            con_docs.append({
                "email": e, "article_id": aid, "source_key": "europepmc",
                "author_name": name, "is_corresponding": True, "unit": unit,
                "unit_verified": verified, "evidence": evidence[:200],
                "verification_authoritative": True,
                "email_kind": _email_kind(e),
            })
        for em in a.get("emails", []):
            e = normalize_email(em)
            if not e or is_noise_email(e) or e in bound or (e, aid) in seen_contacts:
                continue
            dom = e.rsplit("@", 1)[-1].lower()
            if dom in dom_block:
                continue
            seen_contacts.add((e, aid))
            verified, evidence = _verify_person_unit(e, corresp_blocks, aff_blocks, unit)
            con_docs.append({
                "email": e, "article_id": aid, "source_key": "europepmc",
                "author_name": None, "is_corresponding": False, "unit": unit,
                "unit_verified": verified, "evidence": evidence[:200],
                "verification_authoritative": True,
                "email_kind": _email_kind(e),
            })
    return art_docs, con_docs


def _openalex_bulk(unit: str, max_articles: int = 400, per_page: int = 200,
                   enrich_orcid_email: bool = False) -> dict:
    """机构级 OpenAlex 分页(无方向)：补充文章题录与通讯作者 ORCID 公开邮箱。"""
    cands = _resolve_institution(unit)
    if not cands:
        return {"error": f"未解析到单位: {unit}", "articles": []}
    inst = next(
        (
            candidate
            for candidate in cands
            if _institution_candidate_matches(unit, candidate)
        ),
        None,
    )
    if not inst:
        return {
            "error": f"OpenAlex 机构候选与目标单位不一致: {unit}",
            "articles": [],
        }
    inst_id = inst["id"]
    articles: list[dict[str, Any]] = []
    cursor = "*"
    fetched = 0
    while fetched < max_articles:
        page = min(per_page, max_articles - fetched)
        url = (
            f"https://api.openalex.org/works?"
            f"filter=authorships.institutions.id:{inst_id}"
            f"&sort=cited_by_count:desc&per-page={page}&cursor={urllib.parse.quote(cursor)}"
            f"&select=title,doi,publication_year,authorships,primary_location,cited_by_count"
        )
        d = _get(url)
        results = d.get("results", [])
        if not results:
            break
        for w in results:
            corr = []
            for a in w.get("authorships", []):
                if not a.get("is_corresponding"):
                    continue
                au = a.get("author", {})
                emails = (
                    _orcid_public_email(au.get("orcid")) if enrich_orcid_email else []
                )
                corr.append({
                    "name": au.get("display_name"),
                    "orcid": au.get("orcid"),
                    "public_emails": emails,
                })
            articles.append({
                "title": w.get("title"),
                "doi": w.get("doi"),
                "year": w.get("publication_year"),
                "cited_by": w.get("cited_by_count"),
                "landing_page": (w.get("primary_location") or {}).get("landing_page_url"),
                "corresponding": corr,
            })
        fetched += len(results)
        cursor = d.get("meta", {}).get("next_cursor")
        if not cursor:
            break
    return {"unit": inst, "institution_candidates": cands,
            "count": inst.get("works"), "articles": articles}


def discover_bulk(unit: str, unit_en: str = "", max_articles: int = 2000,
                  openalex_max: int = 400, enrich_orcid_email: bool = False,
                  progress=None) -> dict:
    """
    机构级全量入口(无方向)：EuropePMC 分页深抓通讯邮箱为主，OpenAlex 分页补题录。
    输出结构与 discover() 兼容，可直接喂给 normalize_to_docs()。
    """
    ue = unit_en or unit
    try:
        api_results = _openalex_bulk(unit, openalex_max, enrich_orcid_email=enrich_orcid_email)
    except Exception as e:  # noqa: BLE001
        api_results = {"error": str(e), "articles": []}
    try:
        ep = _europepmc_bulk(ue, max_articles=max_articles, progress=progress)
    except Exception as e:  # noqa: BLE001
        ep = {"error": str(e), "articles": []}
    return {
        "unit": unit,
        "unit_en": ue,
        "direction": "",
        "api_results": api_results,
        "email_extraction": {"unit": ue, "direction": "", "sources": {"europepmc": ep}},
        "policy": "仅按文章绑定的公开学术联系渠道; 不导出整单位联系方式名单; 不取个人电话",
    }


# ════════════════════════════════════════════════════════════
# 归一化 -> 可入库实体
# ════════════════════════════════════════════════════════════

def _article_id(doi, pmcid, title) -> str:
    if doi:
        return doi.lower().replace("https://doi.org/", "").strip("/")
    if pmcid:
        return pmcid
    return "title:" + re.sub(r"\s+", " ", (title or "")).strip().lower()[:80]


def _parse_corresp(corresp_list) -> list[tuple[Optional[str], str]]:
    """从 EuropePMC <corresp> 文本解析 姓名->邮箱 绑定。"""
    pairs: list[tuple[Optional[str], str]] = []
    for c in corresp_list or []:
        for seg in re.split(r"[;；]", c):
            em = EMAIL_RE.search(seg)
            if not em:
                continue
            email = em.group(0)
            pre = seg[:em.start()]
            pre = re.sub(r"(correspondence|to whom|address|\*|:|,)", " ",
                         pre, flags=re.I)
            name = re.sub(r"\s+", " ", pre).strip() or None
            pairs.append((name, email))
    return pairs


def _default_sources() -> dict[str, Source]:
    return {
        "openalex": Source("openalex", "OpenAlex", "api", "global", "verified"),
        "orcid": Source("orcid", "ORCID", "api", "global", "verified"),
        "pubmed": Source("pubmed", "PubMed E-utilities", "api", "global", "verified"),
        "europepmc": Source("europepmc", "Europe PMC", "api", "global", "verified"),
        "doaj": Source("doaj", "DOAJ", "api", "global", "verified"),
        "crossref": Source("crossref", "CrossRef", "api", "global", "verified"),
        "openaire": Source("openaire", "OpenAIRE", "api", "global", "verified"),
    }


def normalize_to_docs(discover_output: dict) -> tuple[list[Source], list[Article], list[Contact]]:
    """把 discover() 输出归一化为 (sources, articles, contacts)。"""
    unit = discover_output.get("unit")
    direction = discover_output.get("direction")
    articles: dict[str, Article] = {}
    contacts: dict[tuple[str, str], Contact] = {}

    def upsert_article(
        doi,
        pmcid,
        title,
        year=None,
        src=None,
        landing=None,
        *,
        external_id="",
        unit_verified=False,
        match_evidence="",
    ) -> str:
        aid = str(external_id or "").strip() or _article_id(doi, pmcid, title)
        a = articles.get(aid)
        if not a:
            a = Article(article_id=aid, title=title or "", year=year,
                        doi=(doi or None), pmcid=(pmcid or None),
                        unit=unit, direction=direction, landing_page=landing,
                        unit_verified=bool(unit_verified),
                        match_evidence=str(match_evidence or ""))
            articles[aid] = a
        if src and src not in a.source_keys:
            a.source_keys.append(src)
        if year and not a.year:
            a.year = year
        if landing and not a.landing_page:
            a.landing_page = landing
        if unit_verified:
            a.unit_verified = True
            a.match_evidence = str(match_evidence or a.match_evidence)
        return aid

    def add_contact(
        email,
        aid,
        src,
        name=None,
        corr=False,
        *,
        unit_verified=False,
        verification_authoritative=False,
        evidence="",
        email_kind="",
    ) -> None:
        email = normalize_email(email)
        if not email or is_noise_email(email):
            return
        key = (email, aid)
        c = contacts.get(key)
        if not c:
            contacts[key] = Contact(email=email, article_id=aid, source_key=src,
                                    author_name=name, is_corresponding=corr,
                                    unit=unit, unit_verified=bool(unit_verified),
                                    verification_authoritative=bool(
                                        verification_authoritative
                                    ),
                                    evidence=str(evidence or ""),
                                    email_kind=str(email_kind or _email_kind(email)))
        else:
            if corr:
                c.is_corresponding = True
            if name and not c.author_name:
                c.author_name = name
            if unit_verified:
                c.unit_verified = True
                c.evidence = str(evidence or c.evidence)
            if verification_authoritative:
                c.verification_authoritative = True

    # --- OpenAlex / ORCID ---
    api = discover_output.get("api_results", {}) or {}
    for a in api.get("articles", []):
        aid = upsert_article(a.get("doi"), None, a.get("title"),
                             str(a.get("year") or ""), src="openalex",
                             landing=a.get("landing_page"),
                             unit_verified=api.get("institution_verified", False),
                             match_evidence=api.get("match_evidence", ""))
        for c in a.get("corresponding", []):
            for em in c.get("public_emails", []) or []:
                add_contact(em, aid, "orcid", c.get("name"), corr=True)

    ee = (discover_output.get("email_extraction") or {}).get("sources", {})

    pm = ee.get("pubmed", {})
    for item in pm.get("articles", []):
        doi = item.get("doi")
        pmid = str(item.get("pmid") or "").strip()
        aid = upsert_article(
            doi,
            None,
            item.get("title"),
            str(item.get("year") or ""),
            src="pubmed",
            landing=item.get("landing_page"),
            external_id="" if doi else (f"pubmed:{pmid}" if pmid else ""),
            unit_verified=item.get("unit_verified", False),
            match_evidence=item.get("match_evidence", ""),
        )
        for contact in item.get("contacts", []):
            add_contact(
                contact.get("email"),
                aid,
                "pubmed",
                contact.get("author_name"),
                corr=contact.get("is_corresponding", False),
                unit_verified=contact.get("unit_verified", False),
                verification_authoritative=contact.get(
                    "verification_authoritative",
                    True,
                ),
                evidence=contact.get("evidence", ""),
                email_kind=contact.get("email_kind", ""),
            )

    ep = ee.get("europepmc", {})
    for a in ep.get("articles", []):
        corresp_blocks = a.get("corresp", []) or []
        aff_blocks = a.get("affs", []) or []
        query_unit = discover_output.get("unit_en") or unit
        exact_contacts = a.get("contacts")
        verification = {
            normalize_email(email): _verify_person_unit(
                normalize_email(email), corresp_blocks, aff_blocks, query_unit
            )
            for email in a.get("emails", []) or []
            if normalize_email(email)
        }
        article_verified = bool(a.get("unit_verified", False)) if isinstance(
            exact_contacts, list
        ) else any(item[0] for item in verification.values())
        aid = upsert_article(a.get("doi"), a.get("pmcid"), a.get("title"),
                             str(a.get("year") or ""), src="europepmc",
                             unit_verified=article_verified,
                             match_evidence=(
                                 str(a.get("match_evidence") or "")
                                 if isinstance(exact_contacts, list)
                                 else next(
                                     (
                                         evidence
                                         for verified, evidence in verification.values()
                                         if verified
                                     ),
                                     "",
                                 )
                             ))
        if isinstance(exact_contacts, list):
            for contact in exact_contacts:
                add_contact(
                    contact.get("email"),
                    aid,
                    "europepmc",
                    contact.get("author_name"),
                    corr=contact.get("is_corresponding", False),
                    unit_verified=contact.get("unit_verified", False),
                    verification_authoritative=True,
                    evidence=contact.get("evidence", ""),
                    email_kind=contact.get("email_kind", ""),
                )
            continue
        pairs = _parse_corresp(corresp_blocks)
        bound = {normalize_email(e) for _, e in pairs}
        for name, em in pairs:
            email = normalize_email(em)
            verified, evidence = verification.get(
                email,
                _verify_person_unit(email, corresp_blocks, aff_blocks, query_unit),
            )
            add_contact(
                email,
                aid,
                "europepmc",
                name,
                corr=True,
                unit_verified=verified,
                verification_authoritative=True,
                evidence=evidence,
            )
        for em in a.get("emails", []):
            email = normalize_email(em)
            if email not in bound:
                verified, evidence = verification.get(email, (False, ""))
                add_contact(
                    email,
                    aid,
                    "europepmc",
                    None,
                    corr=False,
                    unit_verified=verified,
                    verification_authoritative=True,
                    evidence=evidence,
                )

    for a in ee.get("doaj", {}).get("articles", []):
        upsert_article(a.get("doi"), None, a.get("title"), src="doaj")

    for a in ee.get("crossref", {}).get("articles", []):
        upsert_article(a.get("doi"), None, a.get("title"),
                       str(a.get("year") or ""), src="crossref")

    # OpenAIRE only exposes response-wide emails here. Without an article URL,
    # they remain discovery hints and are deliberately not persisted as contacts.

    return (list(_default_sources().values()),
            list(articles.values()),
            list(contacts.values()))


def docs_as_dicts(sources, articles, contacts) -> dict[str, list[dict]]:
    return {
        "sources": [asdict(s) for s in sources],
        "articles": [asdict(a) for a in articles],
        "contacts": [asdict(c) for c in contacts],
    }


if __name__ == "__main__":
    import sys
    _unit = sys.argv[1] if len(sys.argv) > 1 else "中山大学附属第一医院"
    _direction = sys.argv[2] if len(sys.argv) > 2 else "nasopharyngeal carcinoma"
    _unit_en = sys.argv[3] if len(sys.argv) > 3 else "Sun Yat-sen"
    out = discover(_unit, _direction, unit_en=_unit_en, limit=5)
    s, a, c = normalize_to_docs(out)
    print(f"Sources={len(s)} Articles={len(a)} Contacts={len(c)}")
    for x in c[:8]:
        tag = "通讯" if x.is_corresponding else "联系"
        print(f"  {x.email:<32} {tag} {x.author_name or '-':<14} {x.source_key} {x.article_id[:30]}")
