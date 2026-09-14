"""Bounded content diffs derived from immutable archive versions."""
import difflib
from api.dao import target_library as dao
from api.dao import source_documents as source_dao
from . import get_target


def text_diff(before: str, after: str) -> dict:
    before, after = str(before or ""), str(after or "")
    limit = 100_000
    lines = list(difflib.unified_diff(before[:limit].splitlines(), after[:limit].splitlines(), fromfile="previous", tofile="selected", lineterm="", n=3))
    return {"lines": lines[:600], "truncated": len(lines) > 600 or len(before) > limit or len(after) > limit,
            "before_length": len(before), "after_length": len(after), "changed": before != after}


async def compare_previous(db, target_id: str, version_id: str) -> dict:
    _, row = await get_target(db, target_id)
    after = await source_dao.get_version(db, version_id)
    if not after or after["document_id"] not in await dao.document_ids(db, row["member_target_ids"]):
        raise ValueError("该版本不属于此单位的归档来源")
    before = await dao.previous_version(db, after)
    if not before:
        return {"before_version_id": None, "after_version_id": version_id, "lines": [], "changed": False, "truncated": False, "message": "这是该来源最早保存的内容版本"}
    return {"before_version_id": before["version_id"], "after_version_id": version_id, "before_captured_at": before.get("captured_at"), "after_captured_at": after.get("captured_at"),
            **text_diff((before.get("content") or {}).get("text", ""), (after.get("content") or {}).get("text", ""))}
