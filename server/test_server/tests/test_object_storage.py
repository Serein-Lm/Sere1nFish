from __future__ import annotations

import hashlib

import pytest


def test_object_key_hierarchy_does_not_expose_owner() -> None:
    from api.storage.keys import build_object_key

    key = build_object_key(
        prefix="sere1nfish/prod",
        kind="payload_word",
        object_id="art_123",
        extension="docx",
        owner="admin@example.com",
        conversation_id="conv_1",
    )

    assert key.startswith("sere1nfish/prod/users/")
    assert key.endswith("/art_123.docx")
    assert "admin" not in key
    assert "@" not in key


def test_oss_access_keys_are_encrypted_and_masked() -> None:
    from api.utils.config_crypto import (
        encrypt_config,
        is_encrypted_value,
        mask_sensitive_config,
    )

    raw = {"access_key_id": "test-access-id", "access_key_secret": "test-secret-value"}
    encrypted = encrypt_config(raw)

    assert is_encrypted_value(encrypted["access_key_id"])
    assert is_encrypted_value(encrypted["access_key_secret"])
    assert mask_sensitive_config(raw) == {
        "access_key_id": "test...s-id",
        "access_key_secret": "test...alue",
    }


@pytest.mark.asyncio
async def test_local_provider_roundtrip(tmp_path) -> None:
    from api.storage.providers.local import LocalStorageProvider

    provider = LocalStorageProvider(tmp_path)
    data = b"object-storage-roundtrip"
    result = await provider.put_bytes(
        "sere1nfish/prod/test/item.bin",
        data,
        content_type="application/octet-stream",
    )
    head = await provider.head("sere1nfish/prod/test/item.bin")

    assert head.size == len(data)
    assert result.etag == hashlib.md5(data, usedforsecurity=False).hexdigest()
    assert await provider.get_bytes("sere1nfish/prod/test/item.bin") == data
    access = await provider.read_access("sere1nfish/prod/test/item.bin")
    assert access.mode == "local"
    assert access.path and access.path.is_file()
    await provider.delete("sere1nfish/prod/test/item.bin")
    assert not access.path.exists()


@pytest.mark.asyncio
async def test_storage_service_streams_files_through_provider(tmp_path, monkeypatch) -> None:
    from api.storage.service import ObjectStorageService
    from api.storage.types import ObjectHead, PutResult
    import api.storage.service as storage_service_module

    source = tmp_path / "payload.bin"
    source.write_bytes(b"streamed-payload" * 1024)

    class Provider:
        name = "fake"
        bucket = "bucket"

        def __init__(self) -> None:
            self.uploaded = False
            self.path = None

        async def put_file(self, key, path, **kwargs):
            self.uploaded = True
            self.path = path
            return PutResult(etag="etag")

        async def head(self, key):
            if not self.uploaded:
                raise FileNotFoundError(key)
            return ObjectHead(size=source.stat().st_size, metadata={"sha256": hashlib.sha256(source.read_bytes()).hexdigest()})

    provider = Provider()
    monkeypatch.setattr(storage_service_module, "get_db", lambda: object())
    monkeypatch.setattr(storage_service_module.storage_dao, "get_object", _async_return(None))
    monkeypatch.setattr(storage_service_module.storage_dao, "create_pending", _async_return({}))
    monkeypatch.setattr(
        storage_service_module.storage_dao,
        "mark_ready",
        _async_return({"object_id": "obj_stream", "status": "ready"}),
    )

    result = await ObjectStorageService({}, provider).store_file(
        source,
        kind="object",
        object_id="obj_stream",
    )

    assert result["status"] == "ready"
    assert provider.path == source.resolve()


@pytest.mark.asyncio
async def test_storage_service_reads_with_object_provider(monkeypatch) -> None:
    from api.storage.service import ObjectStorageService
    import api.storage.service as storage_service_module

    class Provider:
        def __init__(self, name, bucket, payload=b"") -> None:
            self.name = name
            self.bucket = bucket
            self.payload = payload

        async def get_bytes(self, key):
            return self.payload

    active = Provider("aliyun_oss", "remote")
    local = Provider("local", "local", b"legacy-local-object")
    doc = {
        "object_id": "obj_local",
        "provider": "local",
        "bucket": "local",
        "object_key": "legacy/item.bin",
        "status": "ready",
    }
    monkeypatch.setattr(storage_service_module, "get_db", lambda: object())
    monkeypatch.setattr(storage_service_module.storage_dao, "get_object", _async_return(doc))
    monkeypatch.setattr(storage_service_module, "create_storage_provider", lambda config: local)

    service = ObjectStorageService({"provider": "aliyun_oss", "enabled": True}, active)

    assert await service.get_bytes("obj_local") == b"legacy-local-object"


def _async_return(value):
    async def inner(*args, **kwargs):
        return value

    return inner


def test_word_generator_returns_bytes_without_local_path() -> None:
    from api.services.artifact_word import generate_docx

    result = generate_docx(title="OSS Test", content="# Heading\nBody")

    assert result["data"].startswith(b"PK")
    assert result["size"] == len(result["data"])
    assert "file_path" not in result
