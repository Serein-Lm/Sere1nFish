"""Manage GPU node configuration and versioned model bundles.

This CLI is intentionally thin: secrets remain in encrypted runtime config and
all binary artifacts pass through the unified ObjectStorageService.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from api.dao import config as config_dao
from api.dao import storage_objects as storage_dao
from api.db.mongodb import close_mongo, get_db, init_mongo
from api.services.deepfake import get_deepfake_service
from api.storage import get_object_storage


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("Unsupported GPU model bundle manifest")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("GPU model bundle manifest has no artifacts")
    required = {"name", "archive", "object_id", "relative_path"}
    for item in artifacts:
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError("GPU model bundle artifact is incomplete")
        archive = Path(str(item["archive"]))
        relative_path = PurePosixPath(str(item["relative_path"]))
        if archive.is_absolute() or archive.name != str(item["archive"]):
            raise ValueError("GPU model bundle archive must be a filename")
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("GPU model bundle object path is invalid")
        size = int(item.get("size") or 0)
        sha256 = str(item.get("sha256") or "")
        if size < 0 or (sha256 and not _SHA256_PATTERN.fullmatch(sha256)):
            raise ValueError("GPU model bundle digest is invalid")
    return payload


def _file_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _selected_artifacts(
    manifest: dict[str, Any],
    *,
    include_optional: bool,
) -> list[dict[str, Any]]:
    return [
        item
        for item in manifest["artifacts"]
        if include_optional or item.get("required", True)
    ]


def _validate_local_artifact(path: Path, item: dict[str, Any]) -> tuple[int, str]:
    expected_size = int(item.get("size") or 0)
    expected_sha256 = str(item.get("sha256") or "")
    if expected_size <= 0 or not _SHA256_PATTERN.fullmatch(expected_sha256):
        raise ValueError(f"Manifest digest is missing for {item['name']}")
    size, sha256 = _file_digest(path)
    if size != expected_size or sha256 != expected_sha256:
        raise ValueError(f"Local artifact verification failed: {item['name']}")
    return size, sha256


async def _verify_local_bundle(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _load_manifest(Path(args.manifest).resolve())
    archive_dir = Path(args.archive_dir).resolve()
    verified: list[dict[str, Any]] = []
    for item in _selected_artifacts(
        manifest,
        include_optional=bool(args.include_optional),
    ):
        path = archive_dir / str(item["archive"])
        if not path.is_file():
            raise FileNotFoundError(path)
        size, _ = await asyncio.to_thread(_validate_local_artifact, path, item)
        verified.append({"name": item["name"], "archive": path.name, "size": size})
    return {
        "ok": True,
        "bundle_version": manifest.get("bundle_version"),
        "artifacts": verified,
    }


async def _upload_bundle(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).resolve()
    archive_dir = Path(args.archive_dir).resolve()
    manifest = _load_manifest(manifest_path)
    service = await get_object_storage(force_configured_provider=True)
    health = await service.healthcheck()
    if not health.get("ok"):
        raise RuntimeError(health.get("error") or "Object storage is unavailable")

    uploaded: list[dict[str, Any]] = []
    for item in manifest["artifacts"]:
        path = archive_dir / str(item["archive"])
        if not path.is_file():
            if item.get("required", True):
                raise FileNotFoundError(path)
            continue
        size, sha256 = await asyncio.to_thread(_file_digest, path)
        expected_size = int(item.get("size") or 0)
        expected_sha256 = str(item.get("sha256") or "")
        if expected_size and expected_size != size:
            raise ValueError(f"Size mismatch for {path.name}")
        if expected_sha256 and expected_sha256 != sha256:
            raise ValueError(f"SHA-256 mismatch for {path.name}")
        stored = await service.store_file(
            path,
            kind="release",
            filename=path.name,
            object_id=str(item["object_id"]),
            relative_path=str(item["relative_path"]),
            source="gpu_node_bundle",
            source_id=str(manifest.get("bundle_version") or ""),
            meta={
                "component": str(item["name"]),
                "bundle_version": str(manifest.get("bundle_version") or ""),
                "compatibility": manifest.get("compatibility") or {},
            },
        )
        head = await service.head(str(item["object_id"]))
        if head.size != size:
            raise RuntimeError(f"Remote size mismatch for {path.name}")
        item["size"] = size
        item["sha256"] = sha256
        uploaded.append(
            {
                "name": item["name"],
                "object_id": stored["object_id"],
                "size": size,
                "sha256": sha256,
            }
        )

    if args.write_manifest:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return {"ok": True, "bundle_version": manifest.get("bundle_version"), "artifacts": uploaded}


async def _download_bundle(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _load_manifest(Path(args.manifest).resolve())
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = _selected_artifacts(
        manifest,
        include_optional=bool(args.include_optional),
    )
    service = None
    downloaded: list[dict[str, Any]] = []

    for item in selected:
        expected_size = int(item.get("size") or 0)
        expected_sha256 = str(item.get("sha256") or "")
        if expected_size <= 0 or not _SHA256_PATTERN.fullmatch(expected_sha256):
            raise ValueError(f"Manifest digest is missing for {item['name']}")
        destination = output_dir / str(item["archive"])
        if destination.is_file():
            try:
                size, _ = await asyncio.to_thread(
                    _validate_local_artifact,
                    destination,
                    item,
                )
            except ValueError:
                pass
            else:
                downloaded.append({"name": item["name"], "status": "cached", "size": size})
                continue
        if service is None:
            service = await get_object_storage(force_configured_provider=True)
        partial = destination.with_suffix(destination.suffix + ".partial")
        partial.unlink(missing_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            with partial.open("wb") as stream:
                async for chunk in service.iter_bytes(
                    str(item["object_id"]),
                    chunk_size=8 * 1024 * 1024,
                ):
                    stream.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            if size != expected_size or digest.hexdigest() != expected_sha256:
                raise RuntimeError(f"Downloaded artifact verification failed: {item['name']}")
            partial.replace(destination)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        downloaded.append({"name": item["name"], "status": "downloaded", "size": size})

    checksum_lines = [
        f"{item['sha256']}  {item['archive']}"
        for item in selected
        if (output_dir / str(item["archive"])).is_file()
    ]
    (output_dir / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="ascii")
    return {"ok": True, "bundle_version": manifest.get("bundle_version"), "artifacts": downloaded}


async def _verify_bundle(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _load_manifest(Path(args.manifest).resolve())
    service = await get_object_storage(force_configured_provider=True)
    verified: list[dict[str, Any]] = []
    for item in manifest["artifacts"]:
        object_id = str(item["object_id"])
        document = await storage_dao.get_object(get_db(), object_id)
        if not document or document.get("status") != "ready":
            raise FileNotFoundError(f"GPU model artifact is not ready: {object_id}")
        head = await service.head(object_id)
        expected_size = int(item.get("size") or 0)
        if expected_size <= 0 or head.size != expected_size:
            raise RuntimeError(f"GPU model artifact size mismatch: {object_id}")
        if str(document.get("sha256") or "") != str(item.get("sha256") or ""):
            raise RuntimeError(f"GPU model artifact digest mismatch: {object_id}")
        verified.append({"name": item["name"], "object_id": object_id, "size": head.size})
    return {"ok": True, "bundle_version": manifest.get("bundle_version"), "artifacts": verified}


async def _configure(args: argparse.Namespace) -> dict[str, Any]:
    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    ca_certificate = Path(args.ca_file).read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError("GPU API token must contain at least 32 characters")
    if "BEGIN CERTIFICATE" not in ca_certificate:
        raise ValueError("GPU CA certificate is invalid")
    current_doc = await config_dao.get_config(get_db(), "deepfake")
    current = dict((current_doc or {}).get("config") or {})
    updated = {
        **current,
        "provider": "facefusion_gateway",
        "base_url": args.base_url.rstrip("/"),
        "api_token": token,
        "ca_certificate": ca_certificate + "\n",
        "timeout_seconds": int(current.get("timeout_seconds") or 20),
        "max_image_bytes": int(current.get("max_image_bytes") or 12 * 1024 * 1024),
        "max_source_images": int(current.get("max_source_images") or 4),
        "max_voice_reference_bytes": int(
            current.get("max_voice_reference_bytes") or 20 * 1024 * 1024
        ),
        "realtime_max_width": int(current.get("realtime_max_width") or 960),
    }
    await config_dao.set_config(get_db(), "deepfake", updated)
    return {
        "ok": True,
        "base_url": updated["base_url"],
        "api_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "ca_sha256": hashlib.sha256(ca_certificate.encode()).hexdigest(),
    }


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    os.chmod(path, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False)
        stream.write("\n")


async def _backup_config(args: argparse.Namespace) -> dict[str, Any]:
    document = await config_dao.get_config(get_db(), "deepfake")
    payload = {
        "schema_version": 1,
        "exists": document is not None,
        "config": dict((document or {}).get("config") or {}),
    }
    output = Path(args.output).resolve()
    await asyncio.to_thread(_write_private_json, output, payload)
    return {
        "ok": True,
        "exists": payload["exists"],
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }


async def _restore_config(args: argparse.Namespace) -> dict[str, Any]:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("Unsupported GPU config backup")
    if payload.get("exists"):
        config = payload.get("config")
        if not isinstance(config, dict):
            raise ValueError("GPU config backup is invalid")
        await config_dao.set_config(get_db(), "deepfake", config)
    else:
        await config_dao.delete_config(get_db(), "deepfake")
    return {"ok": True, "restored": bool(payload.get("exists"))}


async def _status(_args: argparse.Namespace) -> dict[str, Any]:
    service = await get_deepfake_service()
    payload = await service.status()
    return {"ok": bool(payload.get("ok")), "status": payload}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the Sere1nFish GPU node")
    subparsers = parser.add_subparsers(dest="command", required=True)

    upload = subparsers.add_parser("bundle-upload")
    upload.add_argument("--manifest", required=True)
    upload.add_argument("--archive-dir", required=True)
    upload.add_argument("--write-manifest", action="store_true")

    download = subparsers.add_parser("bundle-download")
    download.add_argument("--manifest", required=True)
    download.add_argument("--output-dir", required=True)
    download.add_argument("--include-optional", action="store_true")

    local_verify = subparsers.add_parser("bundle-local-verify")
    local_verify.add_argument("--manifest", required=True)
    local_verify.add_argument("--archive-dir", required=True)
    local_verify.add_argument("--include-optional", action="store_true")

    verify = subparsers.add_parser("bundle-verify")
    verify.add_argument("--manifest", required=True)

    configure = subparsers.add_parser("configure")
    configure.add_argument("--base-url", required=True)
    configure.add_argument("--token-file", required=True)
    configure.add_argument("--ca-file", required=True)

    backup_config = subparsers.add_parser("config-backup")
    backup_config.add_argument("--output", required=True)

    restore_config = subparsers.add_parser("config-restore")
    restore_config.add_argument("--input", required=True)

    subparsers.add_parser("status")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    handlers = {
        "bundle-upload": _upload_bundle,
        "bundle-download": _download_bundle,
        "bundle-local-verify": _verify_local_bundle,
        "bundle-verify": _verify_bundle,
        "configure": _configure,
        "config-backup": _backup_config,
        "config-restore": _restore_config,
        "status": _status,
    }
    return await handlers[args.command](args)


def main() -> None:
    args = _parser().parse_args()
    init_mongo()
    try:
        result = asyncio.run(_run(args))
        print(json.dumps(result, ensure_ascii=False))
    finally:
        close_mongo()


if __name__ == "__main__":
    main()
