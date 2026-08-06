from __future__ import annotations

from typing import Any
import urllib.error

import pytest

from api.dao import scholar_contact as scholar_dao
from crawler_tools import scholar_tools


def test_scholar_article_url_requires_a_public_source() -> None:
    assert scholar_dao.scholar_article_url({"doi": "10.1000/example"}) == (
        "https://doi.org/10.1000/example"
    )
    assert scholar_dao.scholar_article_url({"pmcid": "pmc12345"}) == (
        "https://europepmc.org/article/PMC/12345"
    )
    assert scholar_dao.scholar_article_url(
        {"landing_page": "https://example.org/article/1"}
    ) == "https://example.org/article/1"
    assert scholar_dao.scholar_article_url({"article_id": "synthetic"}) == ""


def test_email_normalization_does_not_truncate_unknown_cn_subdomains() -> None:
    assert scholar_tools.normalize_email("kzhang6@siii.cas.cn") == (
        "kzhang6@siii.cas.cn"
    )
    assert scholar_tools._clean_emails_in_order(
        "second@example.org then first@example.org then second@example.org"
    ) == ["second@example.org", "first@example.org"]


def test_pubmed_contacts_are_bound_to_article_and_author_evidence() -> None:
    xml = """
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation>
          <PMID>123456</PMID>
          <Article>
            <ArticleTitle>Digital media systems</ArticleTitle>
            <Journal><JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue></Journal>
            <AuthorList><Author>
              <ForeName>Li</ForeName><LastName>Ming</LastName>
              <AffiliationInfo><Affiliation>
                Anhui Broadcasting Corporation, Electronic address: li.ming@media-lab.org
              </Affiliation></AffiliationInfo>
            </Author></AuthorList>
          </Article>
        </MedlineCitation>
        <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/test</ArticleId></ArticleIdList></PubmedData>
      </PubmedArticle>
    </PubmedArticleSet>
    """

    articles = scholar_tools._parse_pubmed_articles(
        xml,
        unit="Anhui Broadcasting Corporation",
    )
    assert articles[0]["landing_page"] == "https://pubmed.ncbi.nlm.nih.gov/123456/"
    assert articles[0]["unit_verified"] is True
    assert articles[0]["contacts"] == [
        {
            "email": "li.ming@media-lab.org",
            "author_name": "Li Ming",
            "is_corresponding": True,
            "unit_verified": True,
            "verification_authoritative": True,
            "evidence": "Anhui Broadcasting Corporation, Electronic address: li.ming@media-lab.org",
            "email_kind": "institutional",
        }
    ]

    _, normalized_articles, normalized_contacts = scholar_tools.normalize_to_docs(
        {
            "unit": "安徽广播电视台",
            "unit_en": "Anhui Broadcasting Corporation",
            "direction": "media technology",
            "api_results": {},
            "email_extraction": {"sources": {"pubmed": {"articles": articles}}},
        }
    )
    assert normalized_articles[0].article_id == "10.1000/test"
    assert normalized_articles[0].landing_page == "https://pubmed.ncbi.nlm.nih.gov/123456/"
    assert normalized_contacts[0].article_id == normalized_articles[0].article_id
    assert normalized_contacts[0].unit_verified is True


def test_pubmed_acronym_does_not_match_only_an_email_domain() -> None:
    xml = """
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation>
          <PMID>789</PMID>
          <Article>
            <ArticleTitle>Carbohydrate research</ArticleTitle>
            <AuthorList><Author>
              <ForeName>Chris</ForeName><LastName>Heiss</LastName>
              <AffiliationInfo><Affiliation>
                Complex Carbohydrate Research Center, University of Georgia.
                Electronic address: cheiss@ccrc.uga.edu.
              </Affiliation></AffiliationInfo>
            </Author></AuthorList>
          </Article>
        </MedlineCitation>
      </PubmedArticle>
    </PubmedArticleSet>
    """

    articles = scholar_tools._parse_pubmed_articles(xml, unit="CCRC")

    assert articles[0]["unit_verified"] is False
    assert articles[0]["contacts"][0]["unit_verified"] is False


def test_pubmed_acronym_matches_a_standalone_affiliation_token() -> None:
    assert scholar_tools._affiliation_matches_unit(
        "China Information and Communication Technologies Group (CICT), Wuhan.",
        "CICT",
    ) is True


def test_europepmc_jats_binds_each_email_to_its_own_author_affiliation() -> None:
    xml = """
    <article>
      <front><article-meta>
        <contrib-group>
          <contrib contrib-type="author" corresp="yes">
            <name><surname>Biao</surname><given-names>Kan</given-names></name>
            <xref ref-type="aff" rid="Aff1" />
            <xref ref-type="corresp" rid="Cor1" />
          </contrib>
          <contrib contrib-type="author">
            <name><surname>Smith</surname><given-names>Alice</given-names></name>
            <xref ref-type="aff" rid="Aff2" />
            <email>alice@mcmaster.ca</email>
          </contrib>
        </contrib-group>
        <aff id="Aff1">
          National Institute for Communicable Disease Control and Prevention,
          Chinese Center for Disease Control and Prevention, Beijing, China.
        </aff>
        <aff id="Aff2">McMaster University, Hamilton, Canada.</aff>
        <author-notes>
          <corresp id="Cor1">Correspondence: kanbiao@icdc.cn</corresp>
        </author-notes>
        <permissions>Figures: figures@plos.org</permissions>
      </article-meta></front>
    </article>
    """

    parsed = scholar_tools._parse_europepmc_full_text(
        xml,
        unit=(
            "National Institute for Communicable Disease Control and Prevention, "
            "Chinese Center for Disease Control and Prevention"
        ),
    )
    contacts = {item["email"]: item for item in parsed["contacts"]}

    assert parsed["unit_verified"] is True
    assert contacts["kanbiao@icdc.cn"]["author_name"] == "Kan Biao"
    assert contacts["kanbiao@icdc.cn"]["unit_verified"] is True
    assert contacts["kanbiao@icdc.cn"]["verification_authoritative"] is True
    assert contacts["alice@mcmaster.ca"]["unit_verified"] is False
    assert contacts["alice@mcmaster.ca"]["evidence"].startswith("McMaster University")
    assert "figures@plos.org" not in contacts


def test_europepmc_shared_correspondence_pairs_emails_without_sharing_units() -> None:
    xml = """
    <article><front><article-meta>
      <contrib-group>
        <contrib contrib-type="author" corresp="yes">
          <name><surname>Zhang</surname><given-names>Ke</given-names></name>
          <xref ref-type="aff" rid="Aff1" />
          <xref ref-type="corresp" rid="Cor1" />
          <email>kzhang6@siii.cas.cn</email>
        </contrib>
        <contrib contrib-type="author" corresp="yes">
          <name><surname>Zheng</surname><given-names>Lishu</given-names></name>
          <xref ref-type="aff" rid="Aff2" />
          <xref ref-type="corresp" rid="Cor1" />
          <email>zhengls@ivdc.chinacdc.cn</email>
        </contrib>
      </contrib-group>
      <aff id="Aff1">Shanghai Institute of Immunity and Infection.</aff>
      <aff id="Aff2">
        National Institute for Viral Disease Control and Prevention,
        Chinese Center for Disease Control and Prevention.
      </aff>
      <corresp id="Cor1">
        Corresponding authors: Shanghai Institute (K. Zhang); National Institute
        for Viral Disease Control and Prevention (L. Zheng).
        kzhang6@siii.cas.cn zhengls@ivdc.chinacdc.cn
      </corresp>
    </article-meta></front></article>
    """

    parsed = scholar_tools._parse_europepmc_full_text(
        xml,
        unit=(
            "National Institute for Viral Disease Control and Prevention, "
            "Chinese Center for Disease Control and Prevention"
        ),
    )
    contacts = {item["email"]: item for item in parsed["contacts"]}

    assert contacts["kzhang6@siii.cas.cn"]["author_name"] == "Ke Zhang"
    assert contacts["kzhang6@siii.cas.cn"]["unit_verified"] is False
    assert contacts["zhengls@ivdc.chinacdc.cn"]["author_name"] == "Lishu Zheng"
    assert contacts["zhengls@ivdc.chinacdc.cn"]["unit_verified"] is True


def test_legacy_europepmc_verification_does_not_use_article_wide_affiliations() -> None:
    verified, evidence = scholar_tools._verify_person_unit(
        "collaborator@example.edu",
        [],
        ["National Institute for Communicable Disease Control and Prevention"],
        "National Institute for Communicable Disease Control and Prevention",
    )

    assert verified is False
    assert evidence == ""


def test_discover_keeps_partial_sources_when_openalex_is_limited(monkeypatch) -> None:
    def fail_openalex(*_args, **_kwargs):
        raise RuntimeError("HTTP 429")

    extracted = {
        "unit": "Example University",
        "direction": "security",
        "sources": {"pubmed": {"articles": []}},
    }
    monkeypatch.setattr(scholar_tools, "_openalex_articles", fail_openalex)
    monkeypatch.setattr(scholar_tools, "_extract_all", lambda *_args: extracted)

    result = scholar_tools.discover(
        "示例大学", "网络安全", "Example University", limit=10
    )

    assert result["api_results"]["error"] == "HTTP 429"
    assert result["email_extraction"] == extracted


def test_scholar_rate_limit_opens_shared_provider_circuit(monkeypatch) -> None:
    calls = 0
    url = "https://api.openalex.org/institutions?search=test"

    def rate_limited(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            url,
            429,
            "Too Many Requests",
            {"Retry-After": "30"},
            None,
        )

    scholar_tools._PROVIDER_CIRCUIT_UNTIL.clear()
    monkeypatch.setattr(scholar_tools.urllib.request, "urlopen", rate_limited)
    try:
        with pytest.raises(urllib.error.HTTPError):
            scholar_tools._get(url)
        with pytest.raises(scholar_tools.ScholarProviderTemporarilyUnavailable):
            scholar_tools._get(url)
        assert calls == 1
    finally:
        scholar_tools._PROVIDER_CIRCUIT_UNTIL.clear()


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def to_list(
        self,
        _length: int | None = None,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        return list(self.rows)


class _Collection:
    def __init__(self) -> None:
        self.count_query: dict[str, Any] = {}
        self.pipelines: list[list[dict[str, Any]]] = []

    def find(self, *_args: Any, **_kwargs: Any) -> _Cursor:
        return _Cursor([])

    def aggregate(self, pipeline: list[dict[str, Any]]) -> _Cursor:
        self.pipelines.append(pipeline)
        return _Cursor([])

    async def count_documents(self, query: dict[str, Any]) -> int:
        self.count_query = query
        return 0


class _Db:
    def __init__(self) -> None:
        self.contacts = _Collection()
        self.articles = _Collection()

    def __getitem__(self, name: str) -> _Collection:
        if name == "scholar_contacts":
            return self.contacts
        if name == "scholar_articles":
            return self.articles
        raise KeyError(name)


@pytest.mark.asyncio
async def test_query_contacts_hides_orphans_before_pagination() -> None:
    db = _Db()

    items, total = await scholar_dao.query_contacts(
        db,  # type: ignore[arg-type]
        "project-1",
        limit=20,
    )

    assert items == []
    assert total == 0
    assert db.contacts.count_query["email"] == {"$nin": [None, ""]}
    assert db.contacts.count_query["article_url"] == {
        "$regex": r"^https?://",
        "$options": "i",
    }
    pipeline = db.contacts.pipelines[0]
    limit_index = pipeline.index({"$limit": 20})
    lookup_index = next(
        index for index, stage in enumerate(pipeline) if "$lookup" in stage
    )
    assert limit_index < lookup_index


@pytest.mark.asyncio
async def test_target_scholar_counts_include_only_verified_contacts() -> None:
    db = _Db()

    result = await scholar_dao.count_contacts_by_target(
        db,  # type: ignore[arg-type]
        project_id="project-1",
        target_ids=["target-1"],
    )

    assert result == {"target-1": 0}
    pipeline = db.contacts.pipelines[0]
    assert "verified_target_ids" in repr(pipeline)
    assert "target_verification.target-1.verified" in repr(pipeline)


@pytest.mark.asyncio
async def test_target_contact_query_prefers_target_specific_verification() -> None:
    db = _Db()

    await scholar_dao.query_contacts(
        db,  # type: ignore[arg-type]
        "project-1",
        target_id="target-1",
        only_verified=True,
        limit=20,
    )

    query = db.contacts.count_query
    assert {"target_ids": "target-1"} in query["$and"][0]["$or"]
    assert {
        "target_verification.target-1.verified": True
    } in query["$and"][1]["$or"]
    legacy_fallback = query["$and"][1]["$or"][1]
    assert legacy_fallback["target_verification"] == {"$exists": False}
    context_stage = db.contacts.pipelines[0][1]["$set"]
    assert context_stage["unit_verified"]["$cond"][2] == (
        "$target_verification.target-1.verified"
    )


@pytest.mark.asyncio
async def test_authoritative_negative_removes_target_from_verified_set() -> None:
    class _Result:
        upserted_id = None
        modified_count = 1

    class _ArticleCollection:
        def find(self, *_args: Any, **_kwargs: Any) -> _Cursor:
            return _Cursor([
                {
                    "article_id": "article-1",
                    "doi": "10.1000/article-1",
                }
            ])

    class _ContactCollection:
        def __init__(self) -> None:
            self.updates: list[dict[str, Any]] = []

        async def update_one(
            self,
            _query: dict[str, Any],
            update: dict[str, Any],
            *,
            upsert: bool,
        ) -> _Result:
            assert upsert is True
            self.updates.append(update)
            return _Result()

    class _WriteDb:
        def __init__(self) -> None:
            self.articles = _ArticleCollection()
            self.contacts = _ContactCollection()

        def __getitem__(self, name: str) -> Any:
            if name == "scholar_articles":
                return self.articles
            if name == "scholar_contacts":
                return self.contacts
            raise KeyError(name)

    db = _WriteDb()
    await scholar_dao.upsert_contacts_batch(
        db,  # type: ignore[arg-type]
        project_id="project-1",
        unit="目标单位",
        direction="传染病",
        target_id="target-1",
        task_id="task-1",
        contacts=[
            {
                "email": "author@example.edu",
                "article_id": "article-1",
                "unit_verified": False,
                "verification_authoritative": True,
                "evidence": "合作大学",
            }
        ],
    )

    update = db.contacts.updates[0]
    context = update["$set"]["target_verification.target-1"]
    assert context["verified"] is False
    assert context["evidence"] == "合作大学"
    assert update["$pull"] == {"verified_target_ids": "target-1"}


@pytest.mark.asyncio
async def test_upsert_contacts_rejects_unlinked_article_ids() -> None:
    db = _Db()

    result = await scholar_dao.upsert_contacts_batch(
        db,  # type: ignore[arg-type]
        project_id="project-1",
        unit="示例单位",
        direction="示例方向",
        contacts=[
            {
                "email": "author@example.org",
                "article_id": "synthetic:query",
            }
        ],
    )

    assert result == {"inserted": 0, "updated": 0, "total": 0}
