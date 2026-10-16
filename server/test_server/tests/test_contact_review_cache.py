"""Attribution reuse is scoped to complete, unchanged source/Target evidence."""
from dataclasses import replace

import pytest

from api.services.source_documents import service
from test_server.tests.test_source_documents import _capture


def context():
    return dict(capture=_capture(raw_html=b"raw", rendered_html=b"dom"),
                contacts=[{"channel": "email", "value": "contact@example.org", "source": "text", "context": "招聘联系"}],
                image_analysis=[], target_name="目标研究院", target_aliases=[], project_id="project", task_id="task",
                target_id="target", version_id="version", keyword="招聘")


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [True, False])
async def test_identical_review_is_reused_including_authoritative_empty_result(monkeypatch, accepted):
    calls = 0

    async def attribute(**kwargs):
        nonlocal calls
        calls += 1
        return kwargs["contacts"] if accepted else [], ""

    monkeypatch.setattr(service, "attribute_target_contacts", attribute)
    first = await service._complete_contextual_analysis({"fields": {"summary": "招聘"}}, **context())
    compact = service._compact_contextual_analysis(first)
    second = await service._complete_contextual_analysis(compact, **context())
    assert calls == 1
    assert second["contact_attribution_cached"] is True
    assert bool(second["target_contacts"]) == accepted


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["target_id", "target_name", "aliases", "version_id", "contacts", "image", "text", "prompt", "policy", "intent"])
async def test_context_or_evidence_change_invalidates_attribution(monkeypatch, change):
    calls = 0

    async def attribute(**kwargs):
        nonlocal calls
        calls += 1
        return kwargs["contacts"], ""

    monkeypatch.setattr(service, "attribute_target_contacts", attribute)
    original = context()
    first = await service._complete_contextual_analysis({"fields": {"summary": "招聘"}}, **original)
    changed = dict(original)
    if change in {"target_id", "target_name", "version_id"}:
        changed[change] += "new"
    elif change == "aliases":
        changed["target_aliases"] = ["可信简称"]
    elif change == "contacts":
        changed["contacts"] = [{**original["contacts"][0], "context": "新增归属上下文"}]
    elif change == "image":
        changed["image_analysis"] = [{"index": 1, "visible_text": "新增证据"}]
    elif change == "text":
        changed["capture"] = replace(original["capture"], text=original["capture"].text + " 新正文")
    elif change == "prompt":
        monkeypatch.setattr(service, "article_analysis_prompt_fingerprint", lambda: "changed-prompt")
    elif change == "policy":
        monkeypatch.setattr(service, "_CONTACT_POLICY_VERSION", 999)
    elif change == "intent":
        changed["keyword"] = "投稿征稿"
    second = await service._complete_contextual_analysis(service._compact_contextual_analysis(first), **changed)
    assert calls == 2
    assert second["contact_attribution_cached"] is False


@pytest.mark.asyncio
async def test_partial_contact_review_error_is_not_cached(monkeypatch):
    calls = 0

    async def attribute(**kwargs):
        nonlocal calls
        calls += 1
        return kwargs["contacts"], "one batch unavailable" if calls == 1 else ""

    monkeypatch.setattr(service, "attribute_target_contacts", attribute)
    first = await service._complete_contextual_analysis({"fields": {}}, **context())
    second = await service._complete_contextual_analysis(first, **context())
    assert calls == 2
    assert second["contact_attribution_error"] == ""


def test_article_cache_fingerprint_changes_when_aliases_change():
    arguments = dict(version_id="version", target_id="target", target_name="Target", keyword="招聘", fields=[])
    assert service._analysis_fingerprint(**arguments, target_aliases=[]) != service._analysis_fingerprint(
        **arguments, target_aliases=["alias"],
    )


@pytest.mark.asyncio
async def test_completed_review_survives_real_context_link_persistence(monkeypatch, isolated_mongo_db):
    from api.dao import source_documents as dao

    db = isolated_mongo_db
    await dao.ensure_indexes(db)
    calls = 0

    async def attribute(**kwargs):
        nonlocal calls
        calls += 1
        return kwargs["contacts"], ""

    monkeypatch.setattr(service, "attribute_target_contacts", attribute)
    first = await service._complete_contextual_analysis({"fields": {"summary": "招聘"}}, **context())
    await dao.link_document(db, document_id="doc", version_id="version", project_id="project", target_id="target",
                            contextual_analysis=service._compact_contextual_analysis(first))
    link = await dao.get_document_link(db, project_id="project", target_id="target", document_id="doc")
    second = await service._complete_contextual_analysis(link["latest_analysis"], **context())
    assert second["contact_attribution_cached"]
    assert calls == 1
    assert await dao.get_document_link(db, project_id="project", target_id="other", document_id="doc") is None
