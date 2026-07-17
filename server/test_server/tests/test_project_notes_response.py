from __future__ import annotations

from bson import ObjectId

from api.routers.project_api import _project_note_out


def test_project_note_response_keeps_stable_mongo_id() -> None:
    mongo_id = ObjectId("6a5a3bb59d858aaa7c6b6f49")
    source = {
        "_id": mongo_id,
        "project_id": "project-1",
        "task_id": "task-1",
        "keyword": "Anhui TV",
        "note_id": "note-1",
    }

    result = _project_note_out(source)

    assert result["id"] == str(mongo_id)
    assert "_id" not in result
    assert source["_id"] == mongo_id
