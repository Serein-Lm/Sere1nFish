"""统一 OSS Object Key 生成规则。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath


_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def safe_segment(value: str, *, fallback: str = "unknown") -> str:
    value = _SAFE_SEGMENT.sub("_", str(value or "").strip()).strip("._-")
    return (value or fallback)[:120]


def owner_hash(owner: str) -> str:
    return hashlib.sha256(str(owner or "anonymous").encode("utf-8")).hexdigest()[:20]


def build_object_key(
    *,
    prefix: str,
    kind: str,
    object_id: str,
    extension: str,
    project_id: str = "",
    owner: str = "",
    conversation_id: str = "",
    subject_id: str = "",
    relative_path: str = "",
    created_at: datetime | None = None,
) -> str:
    """按业务类型生成不包含明文用户信息的层级 Key。"""
    now = created_at or datetime.now(timezone.utc)
    date_parts = [now.strftime("%Y"), now.strftime("%m"), now.strftime("%d")]
    root = [safe_segment(part) for part in PurePosixPath(prefix or "sere1nfish/prod").parts]
    ext = extension.lower().lstrip(".") or "bin"
    filename = f"{safe_segment(object_id)}.{safe_segment(ext, fallback='bin')}"

    if kind == "mobile_screenshot":
        parts = root + ["projects", safe_segment(project_id), "mobile", "screenshots", *date_parts, filename]
    elif kind == "mobile_transfer":
        parts = root + [
            "users",
            owner_hash(owner),
            "mobile",
            "transfers",
            safe_segment(subject_id, fallback="unknown-device"),
            *date_parts,
            filename,
        ]
    elif kind in {"xhs_profile_screenshot", "xhs_note_screenshot"}:
        scope = "note" if kind == "xhs_note_screenshot" else "profile"
        parts = root + ["projects", safe_segment(project_id), "collect", "xhs", scope, safe_segment(subject_id), *date_parts, filename]
    elif kind == "douyin_profile_screenshot":
        parts = root + ["projects", safe_segment(project_id), "collect", "douyin", "profile", safe_segment(subject_id), *date_parts, filename]
    elif kind.startswith("source_document_"):
        artifact = kind.removeprefix("source_document_") or "artifact"
        rel = [safe_segment(part) for part in PurePosixPath(relative_path).parts]
        parts = root + [
            "targets",
            safe_segment(subject_id, fallback="unassigned"),
            "sources",
            *rel,
            safe_segment(artifact),
            filename,
        ]
    elif kind in {"word", "payload_word", "persona_word"}:
        parts = root + ["users", owner_hash(owner), "ai-hub", safe_segment(conversation_id), "artifacts", *date_parts, filename]
    elif kind == "voice_upload":
        parts = root + ["system", "voice", "uploads", *date_parts, filename]
    elif kind == "release":
        rel = [safe_segment(part) for part in PurePosixPath(relative_path).parts]
        parts = root + ["releases", *rel]
    elif kind == "migration_orphan":
        parts = root + ["migration", "orphans", *date_parts, filename]
    else:
        parts = root + ["objects", safe_segment(kind), *date_parts, filename]
    return PurePosixPath(*parts).as_posix()
