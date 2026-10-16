"""Read-only proof that a legacy version still contains committed evidence."""
import hashlib
import json
from typing import Any


async def verify_archived_version(version: dict[str, Any], storage: Any) -> dict[str, Any]:
    artifacts = version.get("artifacts") or {}
    required = [artifacts.get(key) for key in ("raw_html_object_id", "rendered_html_object_id", "structured_object_id")]
    if (not all(required) or not (version.get("content") or {}).get("text")
            or not version.get("identity") or "analysis" not in version):
        return {"readable": False, "reason": "missing_committed_snapshot", "objects_checked": 0}
    references = set(required)
    for key in ("images", "screenshots", "attachments"):
        references.update(item["storage_object_id"] for item in version.get(key) or [] if item.get("storage_object_id"))
    ids = set(version.get("storage_object_ids") or [])
    if not references.issubset(ids):
        return {"readable": False, "reason": "missing_object_references", "objects_checked": 0}
    checked, structured = 0, None
    try:
        for object_id in sorted(ids):
            metadata = await storage.get(object_id)
            if not metadata or metadata.get("status") != "ready" or not metadata.get("sha256"):
                raise ValueError("object_metadata_not_ready")
            expected_size = int(metadata.get("size") or 0)
            if expected_size > 64 * 1024 * 1024:
                raise ValueError("object_exceeds_verification_budget")
            digest, size, chunks = hashlib.sha256(), 0, []
            async for chunk in storage.iter_bytes(object_id):
                size += len(chunk)
                if size > expected_size:
                    raise ValueError("object_size_mismatch")
                digest.update(chunk)
                if object_id == required[2]:
                    chunks.append(chunk)
            if size != expected_size or digest.hexdigest() != metadata["sha256"]:
                raise ValueError("object_digest_mismatch")
            checked += 1
            if object_id == required[2]:
                structured = json.loads(b"".join(chunks))
        if not isinstance(structured, dict):
            raise ValueError("missing_structured_document")
        for key in ("document_id", "version_id", "content_hash"):
            if not version.get(key) or structured.get(key) != version[key]:
                raise ValueError("structured_identity_mismatch")
        if (structured.get("source") or {}).get("content") != version["content"]:
            raise ValueError("structured_content_mismatch")
    except Exception as error:
        # Keep credential-bearing provider errors in their own logs, not repair plans.
        known = {"object_metadata_not_ready", "object_exceeds_verification_budget", "object_size_mismatch",
                 "object_digest_mismatch", "missing_structured_document", "structured_identity_mismatch", "structured_content_mismatch"}
        return {"readable": False, "reason": str(error) if str(error) in known else type(error).__name__,
                "objects_checked": checked}
    return {"readable": True, "reason": "committed_snapshot_and_all_object_digests_verified",
            "objects_checked": checked, "archive_status": version.get("archive_status")}
