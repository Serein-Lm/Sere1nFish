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
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

UA = "acad-collab-finder/1.0 (mailto:contact@example.com)"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# 噪声邮箱：出版社/编辑部/平台，非学者通信邮箱，入库前过滤。
_NOISE_LOCAL = {
    "permissions", "journalpermissions", "info", "support", "service",
    "editor", "editorial", "office", "admin", "webmaster", "contact",
    "help", "noreply", "no-reply",
}
_NOISE_DOMAIN_KEYS = (
    "sciengine.com", "mdpi.com/journal", "elsevier.com", "springer.com",
    "wiley.com", "example.",
)

# 多段 cn 顶级域放最前，避免 chenmy@sysucc.org.cn 被截成 .org。
_TLD_STOP = re.compile(
    r"^([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+?\.(?:edu\.cn|org\.cn|com\.cn|"
    r"gov\.cn|ac\.cn|net\.cn|com|org|net|edu|gov|cn|io|co|de|jp|uk|fr|au|ca))",
    re.I,
)


# ════════════════════════════════════════════════════════════
# HTTP 基础
# ════════════════════════════════════════════════════════════

def _get(url: str, headers: dict | None = None, retries: int = 3,
         timeout: int = 25) -> Any:
    """GET JSON，带重试。代理走环境变量。"""
    hdr = {"User-Agent": UA, **(headers or {})}
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
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


@dataclass
class Contact:
    email: str
    article_id: str
    source_key: str
    author_name: Optional[str] = None
    is_corresponding: bool = False
    unit: Optional[str] = None


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
    return [
        {
            "id": i["id"].rsplit("/", 1)[-1],
            "name": i["display_name"],
            "country": i.get("country_code"),
            "ror": i.get("ror"),
            "works": i["works_count"],
        }
        for i in res
    ]


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
                       enrich_orcid_email: bool) -> dict:
    cands = _resolve_institution(unit)
    if not cands:
        return {"error": f"未解析到单位: {unit}", "articles": []}
    inst = cands[0]
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
        "institution_candidates": cands,
        "direction": direction,
        "count": d.get("meta", {}).get("count"),
        "articles": articles,
    }


# ════════════════════════════════════════════════════════════
# 邮箱抽取源: PubMed / EuropePMC / DOAJ / CrossRef / OpenAIRE
# ════════════════════════════════════════════════════════════

def _pubmed(unit: str, direction: str, retmax: int = 8) -> dict:
    term = f"{unit}[AFFL] AND {direction}"
    u = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
         f"db=pubmed&term={urllib.parse.quote(term)}&retmax={retmax}&retmode=json")
    d = _get(u)
    ids = d["esearchresult"]["idlist"]
    count = d["esearchresult"]["count"]
    emails: list[str] = []
    corresp: list[str] = []
    if ids:
        u2 = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
              f"db=pubmed&id={','.join(ids)}&retmode=xml")
        xml = _get_text(u2)
        emails = _clean_emails(xml)
        corresp = re.findall(r"Electronic address:\s*([^\s<.]+@[^\s<]+)", xml)
    return {"source": "pubmed", "hit_count": count, "pmids": ids,
            "emails": emails, "electronic_address": sorted(set(corresp))}


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
                item["emails"] = _clean_emails(xml)
                corr = re.findall(r"<corresp[^>]*>(.*?)</corresp>", xml, re.S)
                item["corresp"] = [
                    re.sub(r"<[^>]+>", " ", c).strip()[:200] for c in corr
                ]
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
    api_results = _openalex_articles(unit, direction, limit, enrich_orcid_email)

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
                    item["emails"] = _clean_emails(xml)
                    corr = re.findall(r"<corresp[^>]*>(.*?)</corresp>", xml, re.S)
                    item["corresp"] = [
                        re.sub(r"<[^>]+>", " ", c).strip()[:200] for c in corr
                    ]
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
                    item["emails"] = _clean_emails(xml)
                    corr = re.findall(r"<corresp[^>]*>(.*?)</corresp>", xml, re.S)
                    item["corresp"] = [
                        re.sub(r"<[^>]+>", " ", c).strip()[:400] for c in corr
                    ]
                    affs = re.findall(r"<aff[^>]*>(.*?)</aff>", xml, re.S)
                    item["affs"] = [
                        re.sub(r"<[^>]+>", " ", a).strip()[:400] for a in affs
                    ]
                except Exception:  # noqa: BLE001
                    item["emails"] = []
            batch.append(item)
        fetched += len(results)
        yield {"articles": batch, "fetched": fetched, "hit_count": hit_count}
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
    """判断该 email 在这篇文章内是否真的绑定到目标 unit。
    优先看 email 所在的 <corresp> 段；若无 corresp 段，回落到 <aff> 全集。
    返回 (unit_verified, evidence_snippet)。
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
        hl = hosting.lower()
        if any(k in hl for k in neg):
            return False, "NEG:" + hosting[:120]
        for k in pos:
            if k in hl:
                idx = hl.find(k)
                start = max(0, idx - 40); end = min(len(hosting), idx + len(k) + 60)
                return True, hosting[start:end]
        return False, hosting[:120]
    # 2) 无 corresp 段，回落 aff 拼接
    joined = " || ".join(aff_blocks or [])
    jl = joined.lower()
    if any(k in jl for k in neg):
        return False, "NEG-AFF"
    for k in pos:
        if k in jl:
            idx = jl.find(k)
            start = max(0, idx - 40); end = min(len(joined), idx + len(k) + 60)
            return True, joined[start:end]
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
    - 每个 email 必须在其所在 <corresp> 段（或全文 <aff>）里出现 unit 的别名，
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
        art_docs.append({
            "article_id": aid, "title": a.get("title", ""),
            "year": str(a.get("year") or ""), "doi": a.get("doi"),
            "pmcid": a.get("pmcid"), "unit": unit, "direction": "",
            "source_keys": ["europepmc"], "landing_page": None,
        })
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
                "email_kind": _email_kind(e),
            })
    return art_docs, con_docs


def _openalex_bulk(unit: str, max_articles: int = 400, per_page: int = 200,
                   enrich_orcid_email: bool = False) -> dict:
    """机构级 OpenAlex 分页(无方向)：补充文章题录与通讯作者 ORCID 公开邮箱。"""
    cands = _resolve_institution(unit)
    if not cands:
        return {"error": f"未解析到单位: {unit}", "articles": []}
    inst = cands[0]
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

    def upsert_article(doi, pmcid, title, year=None, src=None, landing=None) -> str:
        aid = _article_id(doi, pmcid, title)
        a = articles.get(aid)
        if not a:
            a = Article(article_id=aid, title=title or "", year=year,
                        doi=(doi or None), pmcid=(pmcid or None),
                        unit=unit, direction=direction, landing_page=landing)
            articles[aid] = a
        if src and src not in a.source_keys:
            a.source_keys.append(src)
        if year and not a.year:
            a.year = year
        if landing and not a.landing_page:
            a.landing_page = landing
        return aid

    def add_contact(email, aid, src, name=None, corr=False) -> None:
        email = normalize_email(email)
        if not email or is_noise_email(email):
            return
        key = (email, aid)
        c = contacts.get(key)
        if not c:
            contacts[key] = Contact(email=email, article_id=aid, source_key=src,
                                    author_name=name, is_corresponding=corr,
                                    unit=unit)
        else:
            if corr:
                c.is_corresponding = True
            if name and not c.author_name:
                c.author_name = name

    # --- OpenAlex / ORCID ---
    api = discover_output.get("api_results", {}) or {}
    for a in api.get("articles", []):
        aid = upsert_article(a.get("doi"), None, a.get("title"),
                             str(a.get("year") or ""), src="openalex",
                             landing=a.get("landing_page"))
        for c in a.get("corresponding", []):
            for em in c.get("public_emails", []) or []:
                add_contact(em, aid, "orcid", c.get("name"), corr=True)

    ee = (discover_output.get("email_extraction") or {}).get("sources", {})

    pm = ee.get("pubmed", {})
    for em in pm.get("emails", []):
        add_contact(em, f"pubmed:{unit}:{direction}", "pubmed", corr=False)
    for em in pm.get("electronic_address", []):
        add_contact(em, f"pubmed:{unit}:{direction}", "pubmed", corr=True)

    ep = ee.get("europepmc", {})
    for a in ep.get("articles", []):
        aid = upsert_article(a.get("doi"), a.get("pmcid"), a.get("title"),
                             str(a.get("year") or ""), src="europepmc")
        pairs = _parse_corresp(a.get("corresp", []))
        bound = {normalize_email(e) for _, e in pairs}
        for name, em in pairs:
            add_contact(em, aid, "europepmc", name, corr=True)
        for em in a.get("emails", []):
            if normalize_email(em) not in bound:
                add_contact(em, aid, "europepmc", None, corr=False)

    for a in ee.get("doaj", {}).get("articles", []):
        upsert_article(a.get("doi"), None, a.get("title"), src="doaj")

    for a in ee.get("crossref", {}).get("articles", []):
        upsert_article(a.get("doi"), None, a.get("title"),
                       str(a.get("year") or ""), src="crossref")

    for em in ee.get("openaire", {}).get("emails", []):
        add_contact(em, f"openaire:{unit}:{direction}", "openaire", corr=False)

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
