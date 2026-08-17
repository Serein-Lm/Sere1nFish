from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[2]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from scripts import manage_gpu_node


GATEWAY_ROOT = SERVER_ROOT / "deepfake_gateway"
MANIFEST_PATH = GATEWAY_ROOT / "model-bundle.manifest.json"


def _write_manifest(path: Path, archive: Path, *, required: bool = True) -> None:
    payload = archive.read_bytes()
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bundle_version": "test-v1",
                "artifacts": [
                    {
                        "name": "test_models",
                        "archive": archive.name,
                        "object_id": "obj_gpu_test_models",
                        "relative_path": "gpu-node/test-v1/models.tar.zst",
                        "required": required,
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_committed_gpu_bundle_manifest_is_complete() -> None:
    manifest = manage_gpu_node._load_manifest(MANIFEST_PATH)

    assert manifest["bundle_version"] == "t4-sm75-20260817-v1"
    assert {item["name"] for item in manifest["artifacts"]} == {
        "facefusion_assets",
        "meanvc_models",
        "tensorrt_sm75_cache",
    }
    for item in manifest["artifacts"]:
        assert item["size"] > 0
        assert len(item["sha256"]) == 64


def test_gpu_bundle_manifest_rejects_archive_path_traversal(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifacts": [
                    {
                        "name": "invalid",
                        "archive": "../secret",
                        "object_id": "obj_invalid",
                        "relative_path": "gpu-node/invalid.tar.zst",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be a filename"):
        manage_gpu_node._load_manifest(manifest_path)


def test_gpu_config_snapshot_is_always_private(tmp_path: Path) -> None:
    snapshot = tmp_path / "deepfake-config.json"
    snapshot.write_text("stale", encoding="utf-8")
    snapshot.chmod(0o644)

    manage_gpu_node._write_private_json(snapshot, {"schema_version": 1})

    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600
    assert json.loads(snapshot.read_text(encoding="utf-8")) == {"schema_version": 1}


@pytest.mark.asyncio
async def test_gpu_config_snapshot_roundtrip_uses_config_dao(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = object()
    restored: list[dict[str, object]] = []

    async def get_config(db, category):
        assert db is database
        assert category == "deepfake"
        return {"config": {"base_url": "https://gpu.test", "api_token": "test-token"}}

    async def set_config(db, category, config):
        assert db is database
        assert category == "deepfake"
        restored.append(config)

    monkeypatch.setattr(manage_gpu_node, "get_db", lambda: database)
    monkeypatch.setattr(manage_gpu_node.config_dao, "get_config", get_config)
    monkeypatch.setattr(manage_gpu_node.config_dao, "set_config", set_config)
    snapshot = tmp_path / "deepfake-config.json"

    backup_result = await manage_gpu_node._backup_config(
        argparse.Namespace(output=str(snapshot))
    )
    restore_result = await manage_gpu_node._restore_config(
        argparse.Namespace(input=str(snapshot))
    )

    assert backup_result["ok"] is True
    assert set(backup_result) == {"ok", "exists", "sha256"}
    assert restore_result == {"ok": True, "restored": True}
    assert restored == [{"base_url": "https://gpu.test", "api_token": "test-token"}]


@pytest.mark.asyncio
async def test_bundle_download_uses_verified_local_cache_without_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "models.tar.zst"
    archive.write_bytes(b"verified-model-bundle")
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, archive)

    async def fail_if_storage_is_created(*args, **kwargs):
        raise AssertionError("verified local cache must not access object storage")

    monkeypatch.setattr(manage_gpu_node, "get_object_storage", fail_if_storage_is_created)
    result = await manage_gpu_node._download_bundle(
        argparse.Namespace(
            manifest=str(manifest_path),
            output_dir=str(tmp_path),
            include_optional=False,
        )
    )

    assert result["ok"] is True
    assert result["artifacts"] == [
        {"name": "test_models", "status": "cached", "size": 21}
    ]
    assert (tmp_path / "SHA256SUMS").read_text(encoding="ascii").endswith(
        "  models.tar.zst\n"
    )


def test_gpu_deployment_templates_do_not_pin_retired_infrastructure() -> None:
    paths = [
        GATEWAY_ROOT / "compose.example.yaml",
        GATEWAY_ROOT / "deploy.env.example",
        GATEWAY_ROOT / "DEPLOYMENT.md",
        GATEWAY_ROOT / "deploy" / "deploy-from-main.sh",
        GATEWAY_ROOT / "deploy" / "sere1nfish-gpu-tunnel.service.tpl",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "121.40.145.239" not in combined
    assert "43.106.0.54" not in combined
    assert "ports:" not in (GATEWAY_ROOT / "compose.example.yaml").read_text(
        encoding="utf-8"
    )
    assert "__GPU_SSH_HOST__" in combined
