from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from api.services import mobile_transfer as module


class _Upload:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self.offset >= len(self.data):
            return b""
        end = len(self.data) if size < 0 else self.offset + size
        chunk = self.data[self.offset:end]
        self.offset += len(chunk)
        return chunk


class _Storage:
    async def store_file(self, path: Path, **kwargs: Any) -> dict[str, Any]:
        assert path.read_bytes() == b"image-data"
        assert kwargs["kind"] == "mobile_transfer"
        return {"object_id": kwargs["object_id"]}


class _Transport:
    def __init__(self) -> None:
        self.local_path: Path | None = None
        self.remote_path = ""

    async def push(self, **kwargs: Any) -> dict[str, Any]:
        self.local_path = kwargs["local_path"]
        self.remote_path = kwargs["remote_path"]
        assert self.local_path.read_bytes() == b"image-data"
        return {"adb_endpoint": "10.0.0.2:5555", "remote_path": self.remote_path}


@pytest.mark.asyncio
async def test_upload_archives_pushes_and_removes_temp(monkeypatch: pytest.MonkeyPatch) -> None:
    records: dict[str, dict[str, Any]] = {}

    async def create_transfer(_db: Any, **doc: Any) -> dict[str, Any]:
        records[doc["transfer_id"]] = {**doc, "attempts": 0}
        return records[doc["transfer_id"]]

    async def get_transfer(_db: Any, transfer_id: str) -> dict[str, Any] | None:
        return records.get(transfer_id)

    async def mark_archived(_db: Any, transfer_id: str, object_id: str) -> None:
        records[transfer_id]["storage_object_id"] = object_id

    async def mark_started(_db: Any, transfer_id: str) -> None:
        records[transfer_id]["attempts"] += 1

    async def mark_completed(
        _db: Any, transfer_id: str, *, remote_path: str, adb_endpoint: str
    ) -> dict[str, Any]:
        records[transfer_id].update(
            status="completed", remote_path=remote_path, adb_endpoint=adb_endpoint
        )
        return records[transfer_id]

    monkeypatch.setattr(module.transfers_dao, "create_transfer", create_transfer)
    monkeypatch.setattr(module.transfers_dao, "get_transfer", get_transfer)
    monkeypatch.setattr(module.transfers_dao, "mark_archived", mark_archived)
    monkeypatch.setattr(module.transfers_dao, "mark_push_started", mark_started)
    monkeypatch.setattr(module.transfers_dao, "mark_completed", mark_completed)
    monkeypatch.setattr(module, "get_object_storage", lambda: _async_value(_Storage()))

    transport = _Transport()
    service = module.MobileTransferService(object(), transport=transport)
    result = await service.upload_and_push(
        device_id="serial-1",
        owner="admin",
        filename="sample image.png",
        content_type="image/png",
        category="auto",
        upload=_Upload(b"image-data"),
    )

    assert result["status"] == "completed"
    assert transport.remote_path.startswith("/sdcard/Pictures/Sere1nFish/")
    assert transport.remote_path.endswith(".png")
    assert transport.local_path is not None
    assert not transport.local_path.exists()


async def _async_value(value: Any) -> Any:
    return value


def test_category_detection_and_remote_filename_are_stable() -> None:
    assert module._resolve_category("auto", "voice.MP3", "") == "audio"
    assert module._resolve_category("auto", "photo.jpg", "application/octet-stream") == "image"
    assert module._resolve_category("auto", "report.docx", "") == "attachment"
    assert module._safe_remote_filename("报告 final!.pdf", "mt_1234567890").endswith("_34567890.pdf")
