"""对象存储 Provider 工厂。"""

from __future__ import annotations

import os
from pathlib import Path

from api.storage.providers.aliyun_oss import AliyunOSSProvider
from api.storage.providers.local import LocalStorageProvider
from api.storage.types import StorageProvider


def create_storage_provider(config: dict[str, object]) -> StorageProvider:
    provider = str(config.get("provider") or "local").strip().lower()
    enabled = bool(config.get("enabled", False))
    if provider == "aliyun_oss" and enabled:
        return AliyunOSSProvider(config)
    root = Path(str(
        config.get("local_root")
        or os.getenv("OBJECT_STORAGE_LOCAL_ROOT")
        or (Path.cwd() / "data" / "object_storage")
    ))
    return LocalStorageProvider(root)
