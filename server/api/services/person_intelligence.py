"""人物 OSINT 情报统一领域服务。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import person_intelligence as intelligence_dao
from api.dao import persons as persons_dao
from api.dao import targets as targets_dao
from api.models.person_intelligence import PersonIntelligencePayload
from api.services.source_documents.urls import canonicalize_source_url


def _canonical_url(value: str) -> str:
    return canonicalize_source_url(str(value or "").strip())


def _parse_signal_time(value: str, *, field: str) -> datetime:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} 必须是 ISO 8601 日期或时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_payload(
    payload: PersonIntelligencePayload,
    *,
    allowed_source_urls: set[str] | None = None,
) -> dict[str, Any]:
    data = payload.model_dump()
    sources_by_url: dict[str, dict[str, Any]] = {}
    for source in data["sources"]:
        canonical = _canonical_url(source["url"])
        sources_by_url[canonical] = {**source, "url": canonical}
    if not sources_by_url:
        raise ValueError("人物情报至少需要一个可核验的公开来源")

    known_urls = set(sources_by_url)
    known_urls.update(
        _canonical_url(url) for url in (allowed_source_urls or set()) if str(url).strip()
    )
    contacts: list[dict[str, Any]] = []
    for contact in data.get("public_contacts") or []:
        source_url = _canonical_url(contact["source_url"])
        if source_url not in known_urls:
            raise ValueError("公开联系方式必须引用 sources 中已核验的来源")
        contacts.append({**contact, "source_url": source_url})

    evidence: list[dict[str, Any]] = []
    for item in data.get("evidence") or []:
        source_urls = list(
            dict.fromkeys(_canonical_url(url) for url in item.get("source_urls") or [])
        )
        if not source_urls:
            raise ValueError("每条人物情报证据都必须包含来源 URL")
        if any(url not in known_urls for url in source_urls):
            raise ValueError("证据必须引用 sources 中已核验的来源")
        evidence.append({**item, "source_urls": source_urls})

    context_signals: list[dict[str, Any]] = []
    for signal in data.get("context_signals") or []:
        source_urls = list(
            dict.fromkeys(_canonical_url(url) for url in signal.get("source_urls") or [])
        )
        if any(url not in known_urls for url in source_urls):
            raise ValueError("时间与热点信号必须引用 sources 中已核验的来源")
        observed_at = _parse_signal_time(signal["observed_at"], field="observed_at")
        now = datetime.now(timezone.utc)
        if observed_at > now.replace(hour=23, minute=59, second=59):
            raise ValueError("时间与热点信号的 observed_at 不能晚于当前日期")
        expires_at_text = str(signal.get("expires_at") or "").strip()
        if expires_at_text:
            expires_at = _parse_signal_time(expires_at_text, field="expires_at")
            if expires_at < observed_at:
                raise ValueError("时间与热点信号的 expires_at 不能早于 observed_at")
            if len(expires_at_text) == 10:
                expired = expires_at.date() < now.date()
            else:
                expired = expires_at < now
            if expired:
                raise ValueError("时间与热点信号已经过期，不能作为当前信号保存")
        context_signals.append({**signal, "source_urls": source_urls})

    scenarios: list[dict[str, Any]] = []
    for scenario in data.get("scenarios") or []:
        source_urls = list(
            dict.fromkeys(_canonical_url(url) for url in scenario.get("source_urls") or [])
        )
        if any(url not in known_urls for url in source_urls):
            raise ValueError("沟通场景必须引用 sources 中已核验的来源")
        scenarios.append({**scenario, "source_urls": source_urls})

    copywritings: list[dict[str, Any]] = []
    for copywriting in data.get("sample_copywritings") or []:
        source_urls = list(
            dict.fromkeys(_canonical_url(url) for url in copywriting.get("source_urls") or [])
        )
        if any(url not in known_urls for url in source_urls):
            raise ValueError("话术依据必须引用 sources 中已核验的来源")
        copywritings.append({**copywriting, "source_urls": source_urls})

    return {
        **data,
        "name": data["name"].strip(),
        "organization": data["organization"].strip(),
        "sources": list(sources_by_url.values()),
        "public_contacts": contacts,
        "evidence": evidence,
        "context_signals": context_signals,
        "scenarios": scenarios,
        "sample_copywritings": copywritings,
    }


async def save_person_intelligence(
    db: AsyncIOMotorDatabase,
    payload: PersonIntelligencePayload | dict[str, Any],
) -> dict[str, Any]:
    """校验公开证据、解析 Target/Project 关联并增量归并人物情报。"""
    model = (
        payload
        if isinstance(payload, PersonIntelligencePayload)
        else PersonIntelligencePayload.model_validate(payload)
    )
    candidate_id = intelligence_dao.intelligence_id(model.name, model.organization)
    existing = await intelligence_dao.get_intelligence(db, candidate_id)
    existing_source_urls = {
        str(source.get("url") or "")
        for source in (existing or {}).get("sources") or []
        if isinstance(source, dict) and str(source.get("url") or "").strip()
    }
    data = _normalize_payload(model, allowed_source_urls=existing_source_urls)
    personas_by_id: dict[str, dict[str, Any]] = {}
    for match in data.get("recommended_personas") or []:
        person_id = str(match.get("person_id") or "").strip()
        person = await persons_dao.get_person(db, person_id)
        if not person or person.get("is_fictional") is not True:
            raise ValueError(f"推荐人设 {person_id} 不存在或不是虚构人设")
        personas_by_id[person_id] = person
        match["name"] = str(person.get("name") or match.get("name") or person_id)
    for scenario in data.get("scenarios") or []:
        unknown_ids = [
            person_id
            for person_id in scenario.get("persona_ids") or []
            if person_id not in personas_by_id
        ]
        if unknown_ids:
            raise ValueError("沟通场景引用了未匹配的人设：" + "、".join(unknown_ids))
    target = None
    if data.get("target_id"):
        target = await targets_dao.get_target(db, data["target_id"])
        if not target:
            raise ValueError("指定的 target_id 不存在")
    else:
        target = await targets_dao.find_target(db, name=data["organization"])
        if target:
            data["target_id"] = str(target.get("target_id") or "")

    project_ids = [str(data.pop("project_id", "") or "").strip()]
    if target:
        links = await targets_dao.list_target_projects(db, str(target["target_id"]))
        project_ids.extend(str(link.get("project_id") or "") for link in links)
    data["project_ids"] = list(dict.fromkeys(value for value in project_ids if value))
    task_id = str(data.pop("task_id", "") or "").strip()
    data["task_ids"] = [task_id] if task_id else []
    data["intel_id"] = candidate_id
    return await intelligence_dao.upsert_intelligence(db, data)


async def get_person_intelligence(
    db: AsyncIOMotorDatabase, intel_id: str
) -> dict[str, Any] | None:
    item = await intelligence_dao.get_intelligence(db, intel_id)
    return _decorate_signal_status(item) if item else None


async def list_person_intelligence(
    db: AsyncIOMotorDatabase,
    **filters: Any,
) -> tuple[list[dict[str, Any]], int]:
    items, total = await intelligence_dao.search_intelligence(db, **filters)
    if not filters.get("summary_only"):
        items = [_decorate_signal_status(item) for item in items]
    return items, total


def _decorate_signal_status(document: dict[str, Any]) -> dict[str, Any]:
    """读取时计算信号有效性；历史数据保留，状态不持久化以免随时间失真。"""
    result = dict(document)
    now = datetime.now(timezone.utc)
    signals: list[dict[str, Any]] = []
    for raw in result.get("context_signals") or []:
        signal = dict(raw)
        expires_at = str(signal.get("expires_at") or "").strip()
        if not expires_at:
            status = "undated"
        else:
            try:
                expires = _parse_signal_time(expires_at, field="expires_at")
                status = "expired" if (
                    expires.date() < now.date()
                    if len(expires_at) == 10
                    else expires < now
                ) else "active"
            except ValueError:
                status = "invalid"
        signal["status"] = status
        signals.append(signal)
    result["context_signals"] = signals
    result["active_signal_count"] = sum(
        signal.get("status") == "active" for signal in signals
    )
    return result


async def attach_person_intelligence_artifact(
    db: AsyncIOMotorDatabase,
    *,
    intel_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    from api.dao import artifacts as artifacts_dao

    if not await intelligence_dao.get_intelligence(db, intel_id):
        raise ValueError("人物情报不存在")
    if not await artifacts_dao.get_artifact(db, artifact_id):
        raise ValueError("产物不存在")
    result = await intelligence_dao.attach_artifact(
        db, intel_id=intel_id, artifact_id=artifact_id
    )
    if not result:
        raise ValueError("人物情报产物关联失败")
    return result
