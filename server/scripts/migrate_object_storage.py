"""将现有业务文件幂等迁移到统一对象存储。

默认只检查配置与源文件；传 ``--apply`` 才会上传并更新领域文档。
迁移成功后保留本地源文件，待观察期结束再由运维人工清理。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from api.dao import config as config_dao
from api.dao import storage_migrations as migration_dao
from api.dao import storage_objects as storage_dao
from api.db.collections import (
    ARTIFACTS_COLLECTION,
    MOBILE_SCREENSHOTS_COLLECTION,
    STORAGE_MIGRATIONS_COLLECTION,
    STORAGE_OBJECTS_COLLECTION,
)
from api.db.mongodb import close_mongo, get_db, init_mongo
from api.storage import get_object_storage


@dataclass
class Candidate:
    path: Path
    kind: str
    object_id: str
    filename: str
    project_id: str = ""
    owner: str = ""
    conversation_id: str = ""
    subject_id: str = ""
    source: str = ""
    source_id: str = ""
    relative_path: str = ""
    meta: dict[str, Any] | None = None
    on_ready: Callable[[dict[str, Any]], Awaitable[None]] | None = None


class MigrationRunner:
    def __init__(self, *, apply: bool, concurrency: int) -> None:
        self.apply = apply
        self.sem = asyncio.Semaphore(max(1, min(concurrency, 64)))
        self.db = get_db()
        self.run_id = "osm_" + uuid.uuid4().hex[:20]
        self.service = None
        self.referenced_paths: set[Path] = set()

    async def run(self) -> dict[str, Any]:
        config_doc = await config_dao.get_config(self.db, "object_storage")
        config = config_doc.get("config", {}) if config_doc else {}
        safe_config = {
            key: value for key, value in config.items()
            if key not in {"access_key_id", "access_key_secret", "security_token"}
        }
        await migration_dao.ensure_indexes(self.db)
        await storage_dao.ensure_indexes(self.db)
        await migration_dao.start(self.db, self.run_id, {**safe_config, "apply": self.apply})
        self.service = await get_object_storage(force_configured_provider=True)
        if self.apply:
            health = await self.service.healthcheck()
            if not health.get("ok"):
                await migration_dao.add_failure(
                    self.db,
                    self.run_id,
                    {"stage": "healthcheck", "error": health.get("error") or "OSS healthcheck failed"},
                )
                return await migration_dao.finish(self.db, self.run_id, status="failed")

        candidates = []
        candidates.extend(await self._mobile_candidates())
        candidates.extend(await self._artifact_candidates())
        candidates.extend(await self._xhs_candidates())
        candidates.extend(await self._douyin_candidates())
        candidates.extend(self._release_candidates())
        candidates.extend(self._voice_candidates())
        candidates.extend(await self._local_storage_candidates())
        candidates.extend(self._orphan_candidates())
        await migration_dao.progress(self.db, self.run_id, counter="discovered", amount=len(candidates))
        await asyncio.gather(*(self._process(item) for item in candidates))

        run = await self.db[STORAGE_MIGRATIONS_COLLECTION].find_one({"run_id": self.run_id}, {"_id": 0}) or {}
        failed = int((run.get("counters") or {}).get("failed") or 0)
        if not self.apply:
            status = "dry_run_completed" if failed == 0 else "dry_run_partial"
        else:
            status = "completed" if failed == 0 else "partial"
        if self.apply and failed == 0:
            config["enabled"] = True
            config["migration_state"] = "completed"
            config["migration_run_id"] = self.run_id
            await config_dao.set_config(self.db, "object_storage", config)
        return await migration_dao.finish(self.db, self.run_id, status=status)

    async def _process(self, item: Candidate) -> None:
        async with self.sem:
            if not item.path.is_file():
                await migration_dao.add_failure(
                    self.db,
                    self.run_id,
                    {"source": item.source, "source_id": item.source_id, "path": str(item.path), "error": "source missing"},
                )
                return
            size = item.path.stat().st_size
            if not self.apply:
                await migration_dao.progress(self.db, self.run_id, counter="dry_run_ready", bytes_count=size)
                return
            try:
                stored = await self.service.store_file(
                    item.path,
                    kind=item.kind,
                    filename=item.filename,
                    object_id=item.object_id,
                    content_type=mimetypes.guess_type(item.filename)[0] or "application/octet-stream",
                    owner=item.owner,
                    project_id=item.project_id,
                    conversation_id=item.conversation_id,
                    subject_id=item.subject_id,
                    source=item.source,
                    source_id=item.source_id,
                    relative_path=item.relative_path,
                    meta=item.meta or {},
                )
                remote = await self.service.get_bytes(stored["object_id"])
                local_hash = await asyncio.to_thread(self._sha256_file, item.path)
                if hashlib.sha256(remote).hexdigest() != local_hash:
                    raise RuntimeError("download SHA-256 mismatch")
                if item.on_ready:
                    await item.on_ready(stored)
                await migration_dao.progress(
                    self.db,
                    self.run_id,
                    counter="migrated",
                    bytes_count=size,
                )
            except Exception as exc:
                await migration_dao.add_failure(
                    self.db,
                    self.run_id,
                    {"source": item.source, "source_id": item.source_id, "path": str(item.path), "error": str(exc)[:1000]},
                )

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    async def _mobile_candidates(self) -> list[Candidate]:
        items: list[Candidate] = []
        cursor = self.db[MOBILE_SCREENSHOTS_COLLECTION].find(
            {"storage_object_id": {"$in": [None, ""]}},
            {"_id": 0},
        )
        async for doc in cursor:
            path = Path(str(doc.get("file_path") or ""))
            self.referenced_paths.add(path.resolve())
            sid = str(doc.get("screenshot_id") or "")

            async def ready(stored, screenshot_id=sid):
                await self.db[MOBILE_SCREENSHOTS_COLLECTION].update_one(
                    {"screenshot_id": screenshot_id},
                    {"$set": {"storage_object_id": stored["object_id"], "storage_migration_run_id": self.run_id}},
                )

            items.append(Candidate(
                path=path,
                kind="mobile_screenshot",
                object_id=sid,
                filename=f"{sid}.png",
                project_id=str(doc.get("project_id") or ""),
                subject_id=str(doc.get("device_id") or doc.get("contact_id") or ""),
                source="mobile_screenshot",
                source_id=sid,
                meta={"task_id": doc.get("task_id") or "", "created_at": doc.get("created_at") or ""},
                on_ready=ready,
            ))
        return items

    async def _artifact_candidates(self) -> list[Candidate]:
        items: list[Candidate] = []
        cursor = self.db[ARTIFACTS_COLLECTION].find(
            {"storage_object_id": {"$in": [None, ""]}},
            {"_id": 0},
        )
        async for doc in cursor:
            path = Path(str(doc.get("file_path") or ""))
            self.referenced_paths.add(path.resolve())
            aid = str(doc.get("artifact_id") or "")
            meta = doc.get("meta") or {}

            async def ready(stored, artifact_id=aid):
                await self.db[ARTIFACTS_COLLECTION].update_one(
                    {"artifact_id": artifact_id},
                    {"$set": {"storage_object_id": stored["object_id"], "storage_migration_run_id": self.run_id}},
                )

            items.append(Candidate(
                path=path,
                kind=str(doc.get("kind") or "word"),
                object_id=aid,
                filename=str(doc.get("filename") or f"{aid}.docx"),
                project_id=str(meta.get("project_id") or ""),
                owner=str(doc.get("owner") or ""),
                conversation_id=str(meta.get("conversation_id") or ""),
                source="artifact",
                source_id=aid,
                meta={"title": doc.get("title") or ""},
                on_ready=ready,
            ))
        return items

    async def _xhs_candidates(self) -> list[Candidate]:
        items: list[Candidate] = []
        cursor = self.db["xhs_profiles"].find({"screenshot_paths": {"$type": "array", "$ne": []}})
        async for doc in cursor:
            project_id = str(doc.get("project_id") or "")
            user_id = str(doc.get("user_id") or doc.get("xhs_user_id") or "")
            old_paths = list(doc.get("screenshot_paths") or [])
            for index, old in enumerate(old_paths):
                if str(old).startswith("/api/v1/storage/"):
                    continue
                path = Path("/app/data/xhs_screenshots") / Path(str(old)).name
                self.referenced_paths.add(path.resolve())
                seed = f"{project_id}:{user_id}:{index}:{path.name}"
                object_id = "xss_legacy_" + hashlib.sha256(seed.encode()).hexdigest()[:24]

                async def ready(stored, mongo_id=doc["_id"], old_value=old):
                    url = f"/api/v1/storage/objects/{stored['object_id']}/content"
                    await self.db["xhs_profiles"].update_one(
                        {"_id": mongo_id, "screenshot_paths": old_value},
                        {
                            "$addToSet": {"screenshot_object_ids": stored["object_id"]},
                            "$set": {"storage_migration_run_id": self.run_id, "screenshot_paths.$": url},
                        },
                    )

                items.append(Candidate(
                    path=path,
                    kind="xhs_profile_screenshot",
                    object_id=object_id,
                    filename=f"{object_id}{path.suffix.lower() or '.png'}",
                    project_id=project_id,
                    subject_id=user_id,
                    source="xhs_screenshot",
                    source_id=seed,
                    meta={"legacy_path": str(old)},
                    on_ready=ready,
                ))
        return items

    async def _douyin_candidates(self) -> list[Candidate]:
        items: list[Candidate] = []
        cursor = self.db["douyin_profiles"].find({"screenshot_paths": {"$type": "array", "$ne": []}})
        async for doc in cursor:
            project_id = str(doc.get("project_id") or "")
            sec_uid = str(doc.get("sec_uid") or "")
            for index, old in enumerate(list(doc.get("screenshot_paths") or [])):
                if str(old).startswith("/api/v1/storage/"):
                    continue
                path = Path(str(old))
                self.referenced_paths.add(path.resolve())
                seed = f"{project_id}:{sec_uid}:{index}:{path.name}"
                object_id = "dss_legacy_" + hashlib.sha256(seed.encode()).hexdigest()[:24]

                async def ready(stored, mongo_id=doc["_id"], old_value=old):
                    url = f"/api/v1/storage/objects/{stored['object_id']}/content"
                    await self.db["douyin_profiles"].update_one(
                        {"_id": mongo_id, "screenshot_paths": old_value},
                        {
                            "$addToSet": {"screenshot_object_ids": stored["object_id"]},
                            "$set": {"storage_migration_run_id": self.run_id, "screenshot_paths.$": url},
                        },
                    )

                items.append(Candidate(
                    path=path,
                    kind="douyin_profile_screenshot",
                    object_id=object_id,
                    filename=f"{object_id}{path.suffix.lower() or '.png'}",
                    project_id=project_id,
                    subject_id=sec_uid,
                    source="douyin_screenshot",
                    source_id=seed,
                    on_ready=ready,
                ))
        return items

    def _release_candidates(self) -> list[Candidate]:
        root = Path("/srv/downloads")
        items: list[Candidate] = []
        for path in root.glob("mobile/easytier/*/*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.referenced_paths.add(path.resolve())
            items.append(Candidate(
                path=path,
                kind="release",
                object_id="release_" + digest[:24],
                filename=path.name,
                source="release",
                source_id=relative,
                relative_path=relative,
                meta={"sha256": digest},
            ))
        return items

    def _voice_candidates(self) -> list[Candidate]:
        items: list[Candidate] = []
        for path in Path("/app/uploads/voice").glob("*"):
            if not path.is_file() or path.name == ".DS_Store":
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.referenced_paths.add(path.resolve())
            items.append(Candidate(
                path=path,
                kind="voice_upload",
                object_id="voice_legacy_" + digest[:24],
                filename=path.name,
                source="voice_upload",
                source_id=path.name,
            ))
        return items

    async def _local_storage_candidates(self) -> list[Candidate]:
        items: list[Candidate] = []
        cursor = self.db[STORAGE_OBJECTS_COLLECTION].find({"provider": "local", "status": "ready"}, {"_id": 0})
        async for doc in cursor:
            path = Path("/app/data/object_storage") / str(doc.get("object_key") or "")
            self.referenced_paths.add(path.resolve())
            items.append(Candidate(
                path=path,
                kind=str(doc.get("kind") or "object"),
                object_id=str(doc.get("object_id") or ""),
                filename=str(doc.get("filename") or path.name),
                project_id=str(doc.get("project_id") or ""),
                owner=str(doc.get("owner") or ""),
                subject_id=str((doc.get("meta") or {}).get("subject_id") or ""),
                source=str(doc.get("source") or "local_storage"),
                source_id=str(doc.get("source_id") or doc.get("object_id") or ""),
                relative_path=str((doc.get("meta") or {}).get("relative_path") or ""),
                meta=doc.get("meta") or {},
            ))
        return items

    def _orphan_candidates(self) -> list[Candidate]:
        items: list[Candidate] = []
        roots = [Path("/app/data/artifacts"), Path("/app/data/xhs_screenshots"), Path("/app/data/douyin_screenshots")]
        for root in roots:
            for path in root.glob("**/*"):
                if not path.is_file() or path.name == ".DS_Store" or path.resolve() in self.referenced_paths:
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                items.append(Candidate(
                    path=path,
                    kind="migration_orphan",
                    object_id="orphan_" + digest[:24],
                    filename=path.name,
                    source="migration_orphan",
                    source_id=str(path),
                    meta={"original_path": str(path)},
                ))
        return items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate local business files to object storage")
    parser.add_argument("--apply", action="store_true", help="upload and update MongoDB")
    parser.add_argument("--concurrency", type=int, default=16)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    init_mongo()
    try:
        report = await MigrationRunner(apply=args.apply, concurrency=args.concurrency).run()
        print({
            "run_id": report.get("run_id"),
            "status": report.get("status"),
            "counters": report.get("counters") or {},
            "failures": len(report.get("failures") or []),
        })
    finally:
        close_mongo()


if __name__ == "__main__":
    asyncio.run(main())
