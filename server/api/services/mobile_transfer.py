"""统一手机文件传递服务：临时接收、OSS 归档、ADB 下发与重试。"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Protocol

import aiofiles
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import mobile_transfers as transfers_dao
from api.storage import get_object_storage
from core.logger import get_logger
from core.mobile import MobileDeviceManager
from core.observability import obs_log

logger = get_logger("mobile_transfer")

_CHUNK_SIZE = 1024 * 1024
_DEFAULT_MAX_BYTES = 512 * 1024 * 1024
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif"}
_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac", ".amr"}


class AsyncUpload(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


class MobileTransferError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class MobileTransferTransport(Protocol):
    async def push(
        self,
        *,
        device_id: str,
        local_path: Path,
        remote_path: str,
        timeout: int,
    ) -> dict[str, Any]: ...


class AdbMobileTransferTransport:
    """ADB 传输适配器，业务层不感知设备当前 transport endpoint。"""

    def __init__(self, manager: MobileDeviceManager | None = None) -> None:
        self.manager = manager or MobileDeviceManager()

    @staticmethod
    def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    async def push(
        self,
        *,
        device_id: str,
        local_path: Path,
        remote_path: str,
        timeout: int,
    ) -> dict[str, Any]:
        endpoint = self.manager.resolve_adb_device_id(device_id)
        remote_dir = remote_path.rsplit("/", 1)[0]

        def _push() -> dict[str, Any]:
            state = self._run(["adb", "-s", endpoint, "get-state"], min(timeout, 15))
            if state.returncode != 0 or state.stdout.strip() != "device":
                detail = state.stderr.strip() or state.stdout.strip() or "设备不在线"
                raise MobileTransferError(f"ADB 设备不可用: {detail}")

            mkdir = self._run(
                ["adb", "-s", endpoint, "shell", "mkdir", "-p", remote_dir],
                min(timeout, 30),
            )
            if mkdir.returncode != 0:
                raise MobileTransferError(
                    f"创建手机目录失败: {mkdir.stderr.strip() or mkdir.stdout.strip()}"
                )

            pushed = self._run(
                ["adb", "-s", endpoint, "push", str(local_path), remote_path],
                timeout,
            )
            if pushed.returncode != 0:
                raise MobileTransferError(
                    f"文件发送失败: {pushed.stderr.strip() or pushed.stdout.strip()}"
                )

            scan = self._run(
                [
                    "adb",
                    "-s",
                    endpoint,
                    "shell",
                    "am",
                    "broadcast",
                    "-a",
                    "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                    "-d",
                    f"file://{remote_path}",
                ],
                min(timeout, 30),
            )
            return {
                "adb_endpoint": endpoint,
                "remote_path": remote_path,
                "push_output": (pushed.stdout or pushed.stderr).strip(),
                "media_scan_ok": scan.returncode == 0,
                "media_scan_error": "" if scan.returncode == 0 else (scan.stderr or scan.stdout).strip(),
            }

        try:
            return await asyncio.to_thread(_push)
        except subprocess.TimeoutExpired as exc:
            raise MobileTransferError(f"文件发送超时（{timeout} 秒）", status_code=504) from exc


def _safe_remote_filename(filename: str, transfer_id: str) -> str:
    source = Path(filename or "attachment.bin").name
    suffix = Path(source).suffix.lower()[:16]
    stem = _SAFE_FILENAME.sub("_", Path(source).stem).strip("._-")[:80] or "attachment"
    return f"{stem}_{transfer_id[-8:]}{suffix}"


def _resolve_category(category: str, filename: str, content_type: str) -> str:
    requested = str(category or "auto").strip().lower()
    if requested not in {"auto", "image", "audio", "attachment"}:
        raise MobileTransferError("不支持的文件分类", status_code=400)
    if requested != "auto":
        return requested
    media_type = str(content_type or "").lower()
    suffix = Path(filename).suffix.lower()
    if media_type.startswith("image/") or suffix in _IMAGE_EXTENSIONS:
        return "image"
    if media_type.startswith("audio/") or suffix in _AUDIO_EXTENSIONS:
        return "audio"
    return "attachment"


def _remote_directory(category: str) -> str:
    return {
        "image": "/sdcard/Pictures/Sere1nFish",
        "audio": "/sdcard/Music/Sere1nFish",
        "attachment": "/sdcard/Download/Sere1nFish",
    }[category]


class MobileTransferService:
    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        *,
        transport: MobileTransferTransport | None = None,
        max_parallel_pushes: int = 4,
    ) -> None:
        self.db = db
        self.transport = transport or AdbMobileTransferTransport()
        self._push_slots = asyncio.Semaphore(max(1, max_parallel_pushes))
        self._device_locks: dict[str, asyncio.Lock] = {}

    async def upload_and_push(
        self,
        *,
        device_id: str,
        owner: str,
        filename: str,
        content_type: str,
        category: str,
        upload: AsyncUpload,
    ) -> dict[str, Any]:
        max_bytes, timeout = await self._limits()
        transfer_id = "mt_" + uuid.uuid4().hex
        resolved_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        resolved_category = _resolve_category(category, filename, resolved_type)
        temp_path = await self._new_temp_path(Path(filename).suffix)
        size = 0
        try:
            async with aiofiles.open(temp_path, "wb") as stream:
                while chunk := await upload.read(_CHUNK_SIZE):
                    size += len(chunk)
                    if size > max_bytes:
                        raise MobileTransferError(
                            f"文件超过上传上限 {max_bytes // (1024 * 1024)} MB",
                            status_code=413,
                        )
                    await stream.write(chunk)
            if size <= 0:
                raise MobileTransferError("上传文件为空", status_code=400)

            await transfers_dao.create_transfer(
                self.db,
                transfer_id=transfer_id,
                device_id=device_id,
                owner=owner,
                filename=Path(filename or "attachment.bin").name,
                content_type=resolved_type,
                category=resolved_category,
                size=size,
            )
            storage = await get_object_storage()
            stored = await storage.store_file(
                temp_path,
                kind="mobile_transfer",
                filename=Path(filename or "attachment.bin").name,
                object_id=transfer_id,
                content_type=resolved_type,
                owner=owner,
                subject_id=device_id,
                source="mobile_transfer",
                source_id=transfer_id,
                meta={"device_id": device_id, "category": resolved_category},
            )
            await transfers_dao.mark_archived(self.db, transfer_id, str(stored["object_id"]))
            return await self._push_temp_file(
                transfer_id=transfer_id,
                device_id=device_id,
                filename=filename,
                category=resolved_category,
                temp_path=temp_path,
                timeout=timeout,
            )
        except MobileTransferError as exc:
            if await transfers_dao.get_transfer(self.db, transfer_id):
                await transfers_dao.mark_failed(self.db, transfer_id, str(exc))
            raise
        except Exception as exc:  # noqa: BLE001
            if await transfers_dao.get_transfer(self.db, transfer_id):
                await transfers_dao.mark_failed(self.db, transfer_id, str(exc))
            raise MobileTransferError(str(exc) or "手机文件传输失败") from exc
        finally:
            await asyncio.to_thread(temp_path.unlink, missing_ok=True)

    async def retry(
        self,
        *,
        transfer_id: str,
        device_id: str,
        owner: str,
        is_admin: bool,
    ) -> dict[str, Any]:
        doc = await transfers_dao.get_transfer(self.db, transfer_id)
        if not doc:
            raise MobileTransferError("传输记录不存在", status_code=404)
        if not is_admin and doc.get("owner") != owner:
            raise MobileTransferError("无权重试该传输", status_code=403)
        if str(doc.get("device_id") or "") != device_id:
            raise MobileTransferError("传输记录不属于当前设备", status_code=409)
        object_id = str(doc.get("storage_object_id") or "")
        if not object_id:
            raise MobileTransferError("归档文件不存在，无法重试", status_code=409)

        _, timeout = await self._limits()
        temp_path = await self._new_temp_path(Path(str(doc.get("filename") or "")).suffix)
        try:
            storage = await get_object_storage()
            async with aiofiles.open(temp_path, "wb") as stream:
                async for chunk in storage.iter_bytes(object_id):
                    await stream.write(chunk)
            return await self._push_temp_file(
                transfer_id=transfer_id,
                device_id=str(doc.get("device_id") or ""),
                filename=str(doc.get("filename") or "attachment.bin"),
                category=str(doc.get("category") or "attachment"),
                temp_path=temp_path,
                timeout=timeout,
            )
        except MobileTransferError as exc:
            await transfers_dao.mark_failed(self.db, transfer_id, str(exc))
            raise
        except Exception as exc:  # noqa: BLE001
            await transfers_dao.mark_failed(self.db, transfer_id, str(exc))
            raise MobileTransferError(str(exc) or "重试失败") from exc
        finally:
            await asyncio.to_thread(temp_path.unlink, missing_ok=True)

    async def list_for_device(
        self,
        *,
        device_id: str,
        owner: str,
        is_admin: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await transfers_dao.list_transfers(
            self.db,
            device_id=device_id,
            owner="" if is_admin else owner,
            limit=limit,
        )

    async def _push_temp_file(
        self,
        *,
        transfer_id: str,
        device_id: str,
        filename: str,
        category: str,
        temp_path: Path,
        timeout: int,
    ) -> dict[str, Any]:
        remote_path = (
            f"{_remote_directory(category)}/"
            f"{_safe_remote_filename(filename, transfer_id)}"
        )
        lock = self._device_locks.setdefault(device_id, asyncio.Lock())
        await transfers_dao.mark_push_started(self.db, transfer_id)
        obs_log(
            "手机文件发送开始",
            source="mobile_transfer",
            event="transfer_start",
            data={
                "transfer_id": transfer_id,
                "device_id": device_id,
                "category": category,
            },
        )
        async with self._push_slots, lock:
            result = await self.transport.push(
                device_id=device_id,
                local_path=temp_path,
                remote_path=remote_path,
                timeout=timeout,
            )
        doc = await transfers_dao.mark_completed(
            self.db,
            transfer_id,
            remote_path=remote_path,
            adb_endpoint=str(result.get("adb_endpoint") or ""),
        )
        obs_log(
            "手机文件发送完成",
            source="mobile_transfer",
            event="transfer_done",
            data={
                "transfer_id": transfer_id,
                "device_id": device_id,
                "remote_path": remote_path,
            },
        )
        return {**(doc or {}), "transport": result}

    @staticmethod
    async def _new_temp_path(suffix: str) -> Path:
        def _create() -> Path:
            fd, name = tempfile.mkstemp(prefix="sere1nfish-mobile-", suffix=suffix[:16])
            os.close(fd)
            return Path(name)

        return await asyncio.to_thread(_create)

    @staticmethod
    async def _limits() -> tuple[int, int]:
        try:
            from api.services.runtime_config import get_runtime_app_config

            mobile = (await get_runtime_app_config()).mobile
            max_bytes = max(1024 * 1024, int(getattr(mobile, "transfer_max_bytes", _DEFAULT_MAX_BYTES)))
            timeout = max(30, int(getattr(mobile, "transfer_timeout_seconds", 300)))
            return max_bytes, timeout
        except Exception:
            return _DEFAULT_MAX_BYTES, 300


_service: MobileTransferService | None = None


def get_mobile_transfer_service(db: AsyncIOMotorDatabase) -> MobileTransferService:
    global _service
    if _service is None:
        _service = MobileTransferService(db)
    return _service
