from unittest.mock import AsyncMock
import pytest
from api.schemas.persona_context import ContextPlan, ContextSlot, FictionalContextProfile
from api.services import persona_context as service
from api.services.persona_quality import _profile_quality_issues
from test_server.tests.test_ai_hub_payload import _rich_fictional_profile


def candidate():
    data = _rich_fictional_profile().model_dump()
    data.update(sources=[], evidence=[], research_evidence=[],
        company_business="虚构区域制造企业，负责工业零部件加工，设有生产、采购、质量、财务与信息化部门，各部门按项目协同。",
        company_address="虚构工业园一号楼三层办公室", company_website="https://sample.example",
        scenario_contact={"origin": "fictional", "phone": "模拟 010-0000-1234 转 201", "email": "office@sample.example", "wechat": "模拟-office-201",
            "availability": "工作日九点至十七点，月末优先留言", "introduction": "先经行政总机登记事项，按项目类型转到部门负责人。"})
    return data


def test_complete_context_needs_no_web_sources_but_preserves_richness():
    profile = FictionalContextProfile.model_validate(candidate()).model_dump()
    assert _profile_quality_issues(profile, require_references=False) == []
    assert "sources 不能为空" in _profile_quality_issues(profile)
    assert "background 缺少完整职业与生活时间线" in _profile_quality_issues({**profile, "background": "简略"}, require_references=False)


def test_materialization_cannot_invent_evidence_or_change_existing_identity():
    raw = candidate()
    raw["sources"] = ["https://invented.example/evidence"]
    fresh = service.materialize(raw, existing=None, generation_key="new", background="背景", scope={})
    assert fresh["sources"] == [] and fresh["information_origin"] == "fictional_context"
    old = {**raw, "generation_key": "stable", "source_urls": ["https://prior.example/evidence"], "age": 40}
    merged = service.materialize(raw, existing=old, generation_key="new", background="背景", scope={})
    assert merged["age"] == 40 and merged["generation_key"] == "stable"
    assert merged["sources"] == old["source_urls"]
    assert merged["scenario_contact"] == old["scenario_contact"]
    assert merged["contact"]["phone"] == ""
    legacy = service.materialize(raw, existing={**old, "generation_key": ""}, generation_key="new", background="背景", scope={})
    assert legacy["generation_key"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [False, True])
async def test_generation_persists_complete_context_or_returns_preview_without_writes(monkeypatch, dry_run):
    raw = candidate()
    plan = ContextPlan(slots=[ContextSlot(name=raw["name"], industry=raw["industry"], position=raw["position"], direction="具体组织职责与生活背景应当保持自洽并提供足够丰富的沟通方式与协作情境。")])
    invoke = AsyncMock(side_effect=[plan, FictionalContextProfile.model_validate(raw)])
    monkeypatch.setattr(service, "_invoke", invoke)
    monkeypatch.setattr("Sere1nGraph.graph.prompts.loader.load_prompt", lambda _: "context prompt")
    write = AsyncMock(side_effect=lambda db, **kw: {**kw["profile"], "person_id": "stable-person"})
    progress = AsyncMock()
    monkeypatch.setattr(service.persons_dao, "upsert_person", write)
    monkeypatch.setattr(service.task_dao, "update_task", progress)
    result = await service.generate_contexts({}, {}, count=1, industries=[raw["industry"]], task_id="test-context", industry_code="13", industry_sector_code="C", dry_run=dry_run)
    assert result["generated"] == 1
    if dry_run:
        write.assert_not_called()
        progress.assert_not_called()
        assert result["preview"][0]["context_complete"]
    else:
        assert result["items"][0]["industry_code"] == "13"
        assert write.call_args.kwargs["source"] == "synthetic_context"


def test_person_dao_keeps_simulated_contact_separate():
    from api.dao.persons import _merge_set_fields
    profile = service.materialize(candidate(), existing=None, generation_key="key", background="背景", scope={"industry_code": "13"})
    fields, _ = _merge_set_fields(None, profile)
    assert fields["scenario_contact"]["origin"] == "fictional"
    assert fields["contact"]["phone"] == ""
    assert fields["context_complete"] and fields["industry_code"] == "13"
