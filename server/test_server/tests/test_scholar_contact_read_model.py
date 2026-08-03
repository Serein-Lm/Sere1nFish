from __future__ import annotations

from typing import Any

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
    assert db.contacts.pipelines[0][0]["$match"]["unit_verified"] is True


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
