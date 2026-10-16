"""Target-scoped contact attribution and evidence-aware result reuse."""
import hashlib
import json
from typing import Any, Awaitable, Callable

from core.mobile.collect.contacts import normalize_contact_candidate
from .analysis import filter_target_contacts, stable_content_hash
from .contracts import CapturedDocument


def review_fingerprint(*, capture: CapturedDocument, **context: Any) -> str:
    payload = {"schema_version": 1, "source_type": capture.source_type,
               "canonical_url": capture.canonical_url, "content_hash": stable_content_hash(capture),
               "title": capture.title, "account": capture.account, **context}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def complete_contextual_analysis(
    analysis: dict[str, Any], *, capture: CapturedDocument,
    contacts: list[dict[str, Any]], image_analysis: list[dict[str, Any]],
    target_name: str, target_aliases: list[str], project_id: str, task_id: str,
    version_id: str, target_id: str, keyword: str, policy_version: int, prompt_hash: str,
    attribute_contacts: Callable[..., Awaitable[tuple[list[dict[str, Any]], str]]],
) -> dict[str, Any]:
    validated_contacts: list[dict[str, Any]] = []
    for contact in contacts:
        if str(contact.get("source") or "text") != "image":
            validated_contacts.append(contact)
            continue
        normalized = normalize_contact_candidate(contact, source="image")
        if normalized:
            validated_contacts.append(
                {
                    **contact,
                    **normalized,
                    "contexts": list(
                        contact.get("contexts") or normalized["contexts"]
                    ),
                }
            )
    contacts = validated_contacts
    fields = dict(analysis.get("fields") or {})
    review_values = list(
        (analysis.get("relevance_review") or {}).get("target_contact_values") or []
    )
    declared_values = review_values or list(analysis.get("target_contact_values") or [])
    reviewed_contacts = filter_target_contacts(
        contacts,
        declared_values,
        text=capture.text,
    )
    fingerprint = review_fingerprint(
        capture=capture, contacts=contacts, image_analysis=image_analysis,
        target_name=target_name, target_aliases=target_aliases, target_id=target_id,
        version_id=version_id, keyword=keyword, project_id=project_id,
        summary=str(fields.get("summary") or ""), policy_version=policy_version, prompt_hash=prompt_hash,
    )
    cache = dict(analysis.get("contact_attribution_cache") or {})
    cached = (cache.get("status") == "complete" and cache.get("fingerprint") == fingerprint
              and isinstance(analysis.get("target_contacts"), list))
    attribution_error = ""
    target_contacts = list(analysis["target_contacts"]) if cached else reviewed_contacts
    if contacts and not cached:
        attributed, attribution_error = await attribute_contacts(
            target_name=target_name, target_aliases=target_aliases, title=capture.title,
            account=capture.account, summary=str(fields.get("summary") or ""),
            contacts=contacts, image_analysis=image_analysis, project_id=project_id,
            task_id=task_id, source_type=capture.source_type,
        )
        if attributed or not attribution_error:
            target_contacts = attributed
    fields.update(
        {
            "title": capture.title,
            "account": capture.account,
            "publish_time": capture.publish_time,
            "content": capture.text,
            "contact": "、".join(
                str(item.get("label") or "") for item in target_contacts
            ),
            "image_context": [
                item.get("description")
                for item in image_analysis
                if item.get("description")
            ],
        }
    )
    return {
        **analysis,
        "fields": fields,
        "target_contacts": target_contacts,
        "contact_attribution_error": attribution_error,
        "contact_attribution_cached": cached,
        "contact_attribution_cache": {"fingerprint": fingerprint, "schema_version": 1,
                                      "status": "partial" if attribution_error else "complete"},
    }

