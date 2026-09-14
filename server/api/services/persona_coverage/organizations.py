"""Public organization facts, verified against immutable archived source text."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone

from api.dao import persona_coverage as dao
from api.schemas.persona_coverage import OrganizationResearchResult


def compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def supported_fact(fact: dict, text: str) -> dict | None:
    """Do not accept a model-provided source URL as evidence by itself."""
    result = dict(fact)
    body, excerpt, name = compact(text), compact(fact.get("excerpt", "")), compact(fact.get("organization_name", ""))
    if not name or name not in excerpt or not excerpt or excerpt not in body:
        return None
    for key in ("website", "address"):
        if compact(result.get(key, "")) not in body:
            result[key] = ""
    phone = compact(result.get("office_phone", "")).replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    phone = phone.removeprefix("+86").replace("—", "-")
    public_number = re.fullmatch(r"(?:0\d{2,3}-?\d{7,8}|(?:400|800)-?\d{3}-?\d{4}|95\d{3,4}|123\d{2})(?:转\d{1,6})?", phone)
    if not public_number or compact(result.get("office_phone", "")) not in excerpt or not re.search(r"电话|热线|总机|联系", excerpt):
        result["office_phone"] = ""
    return result


async def research_organizations(db, app_config, job: dict) -> list[str]:
    from langchain_core.messages import HumanMessage, SystemMessage
    from api.services.persona_research_browser import create_persona_research_browser
    from api.services.source_documents import ingest_source_url, get_source_document_detail
    from api.services.targets import resolve_target
    from core.observability import observation_context
    from Sere1nGraph.graph.agents.runtime import create_llm
    from Sere1nGraph.graph.prompts.loader import load_prompt

    code, name, job_id = job["industry_code"], job["industry_name"], job["job_id"]
    browser = create_persona_research_browser()
    pages = await browser.collect(app_config, search_queries=[f"{name} 企业 官网 联系我们 电话", f"{name} 机构 名录 官方", f"{name} 行业协会 企业 联系电话"], task_id=job_id, research_key=job_id + "_organizations", candidate_offset=(max(1, job.get("attempts", 1)) - 1) * 4)
    with observation_context(task_id=job_id, phase="industry_organization_research", agent="industry_organizations", task_type="persona_coverage"):
        model = create_llm(app_config, workload="collection", streaming=False).with_structured_output(OrganizationResearchResult)
        result = await asyncio.wait_for(model.ainvoke([
            SystemMessage(content=load_prompt("industry_organizations/industry_organizations")),
            HumanMessage(content=json.dumps({"industry_code": code, "industry": name, "pages": [page.model_payload() for page in pages]}, ensure_ascii=False)),
        ]), timeout=180)
    pages_by_url = {page.url: page for page in pages}
    candidates = []
    for item in result.organizations:
        page = pages_by_url.get(item.source_url)
        if page and (validated := supported_fact(item.model_dump(), page.text)):
            candidates.append(validated)
    # Archive each distinct source once; bound the number of expensive captures.
    urls = list(dict.fromkeys(item["source_url"] for item in candidates))[:4]
    semaphore = asyncio.Semaphore(2)
    async def archive(url: str) -> list[str]:
        async with semaphore:
            captured = await ingest_source_url(db, url=url, run_task_id=job_id, keyword=name, discovery_context={"kind": "industry_reference", "industry_code": code})
            if not captured.get("ok"):
                raise RuntimeError("机构资料归档未就绪")
            document = await get_source_document_detail(db, captured["document_id"], version_id=captured["version_id"])
            text = ((document or {}).get("version") or {}).get("content", {}).get("text", "")
            facts = []
            for candidate in candidates:
                if candidate["source_url"] != url or not (fact := supported_fact(candidate, text)):
                    continue
                target = await resolve_target(db, target_name=fact["organization_name"], target_type=fact["organization_type"], source="industry_research")
                if not target:
                    continue
                identity = json.dumps([code, target["target_id"], captured["version_id"], fact.get("office_phone"), fact.get("website"), fact.get("address")], ensure_ascii=False)
                facts.append({**fact, "fact_id": "iof_" + hashlib.sha256(identity.encode()).hexdigest()[:24], "industry_code": code, "sector_code": job["sector_code"], "target_id": target["target_id"], "source_document_id": captured["document_id"], "source_document_version_id": captured["version_id"], "job_id": job_id, "captured_at": datetime.now(timezone.utc), "evidence_status": "archived_text_verified", "identity_relation": "industry_reference_only"})
            return await dao.save_facts(db, facts)
    results = await asyncio.gather(*(archive(url) for url in urls), return_exceptions=True)
    saved = [item for result in results if isinstance(result, list) for item in result]
    if not saved:
        errors = [str(result) for result in results if isinstance(result, Exception)]
        raise RuntimeError("未找到可归档核验的行业机构资料" + ("：" + errors[0][:200] if errors else ""))
    return saved
