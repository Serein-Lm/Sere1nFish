#!/usr/bin/env python3
"""
数据库导出 & 迁移脚本 — 打包数据库全量数据，支持跨服务器迁移

用法:
    cd Sere1nFishServer

    # 导出全量数据库到 JSON
    python -m scripts.db_migrate export
    python -m scripts.db_migrate export --output ./backup/db_20260531.json

    # 仅导出指定集合
    python -m scripts.db_migrate export --collections skills,prompts,system_config

    # 直接迁移到另一个 MongoDB（无需中间文件）
    python -m scripts.db_migrate clone --target-uri mongodb://new-host:27017 --target-user root --target-pass xxx --target-db Sere1nG0Fish

    # 从 JSON 文件导入到目标数据库
    python -m scripts.db_migrate import --input ./backup/db_20260531.json
    python -m scripts.db_migrate import --input ./backup/db_20260531.json --target-uri mongodb://new-host:27017 --target-user root --target-pass xxx --target-db Sere1nG0Fish

    # 导入时清空目标集合再写入（慎用）
    python -m scripts.db_migrate import --input ./backup/db_20260531.json --drop-before-import
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId


SKIP_COLLECTIONS = {"system.sessions", "system.version"}


class BSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return {"$oid": str(o)}
        if isinstance(o, datetime):
            return {"$date": o.isoformat()}
        return super().default(o)


def _decode_special(obj: dict):
    if "$oid" in obj:
        return ObjectId(obj["$oid"])
    if "$date" in obj:
        return datetime.fromisoformat(obj["$date"])
    return obj


def _json_hook(obj: dict):
    return _decode_special(obj)


def _source_settings():
    from api.config import get_settings

    return get_settings()


def _client_kwargs_from_settings(settings):
    kwargs = {
        "authSource": settings.MONGODB_AUTH_SOURCE,
        "appname": settings.MONGODB_APPNAME,
        "maxPoolSize": settings.MONGODB_MAX_POOL_SIZE,
        "minPoolSize": settings.MONGODB_MIN_POOL_SIZE,
        "maxIdleTimeMS": settings.MONGODB_MAX_IDLE_TIME_MS,
        "serverSelectionTimeoutMS": settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS,
        "connectTimeoutMS": settings.MONGODB_CONNECT_TIMEOUT_MS,
    }
    if settings.MONGODB_USERNAME:
        kwargs["username"] = settings.MONGODB_USERNAME
    if settings.MONGODB_PASSWORD:
        kwargs["password"] = settings.MONGODB_PASSWORD
    if settings.MONGODB_DIRECT:
        kwargs["directConnection"] = True
    return kwargs


def _get_source_client():
    settings = _source_settings()
    return AsyncIOMotorClient(settings.MONGODB_URI, **_client_kwargs_from_settings(settings))


def _get_target_client(
    uri: str,
    username: str | None = None,
    password: str | None = None,
    auth_source: str = "admin",
    direct: bool = False,
):
    kwargs = {"authSource": auth_source}
    if username:
        kwargs["username"] = username
    if password:
        kwargs["password"] = password
    if direct:
        kwargs["directConnection"] = True
    return AsyncIOMotorClient(uri, **kwargs)


async def _list_all_collections(db) -> list[str]:
    """列出数据库中所有集合（排除系统集合）"""
    names = await db.list_collection_names()
    return sorted([n for n in names if n not in SKIP_COLLECTIONS and not n.startswith("system.")])


async def cmd_export(args):
    settings = _source_settings()
    client = _get_source_client()
    db = client[settings.MONGODB_DATABASE]

    if args.collections:
        collections = args.collections.split(",")
    else:
        collections = await _list_all_collections(db)

    print("=" * 50)
    print("  数据库全量导出")
    print(f"  源: {settings.MONGODB_URI}/{settings.MONGODB_DATABASE}")
    print(f"  集合数: {len(collections)}")
    print("=" * 50)

    export_data = {
        "_meta": {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "database": settings.MONGODB_DATABASE,
            "collections": collections,
        },
        "collections": {},
    }

    total_docs = 0
    for col_name in collections:
        docs = []
        async for doc in db[col_name].find():
            docs.append(doc)
        export_data["collections"][col_name] = docs
        total_docs += len(docs)
        print(f"  {col_name}: {len(docs)} 条")

    output_path = Path(args.output) if args.output else ROOT / "backup" / f"db_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, cls=BSONEncoder, ensure_ascii=False, indent=2)

    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"\n✅ 导出完成: {output_path}")
    print(f"   总计: {total_docs} 条文档, {size_mb:.2f} MB")

    client.close()


def _build_target_client(args):
    """根据命令行参数构建目标 DB 客户端"""
    if args.target_uri:
        client = _get_target_client(
            args.target_uri,
            username=getattr(args, "target_user", None),
            password=getattr(args, "target_pass", None),
            auth_source=getattr(args, "target_auth_source", "admin") or "admin",
            direct=getattr(args, "target_direct", False),
        )
        db_name = args.target_db or "Sere1nG0Fish"
        return client, db_name
    else:
        settings = _source_settings()
        client = _get_source_client()
        return client, settings.MONGODB_DATABASE


async def cmd_import(args):
    if not args.input:
        print("❌ 请指定 --input 参数")
        return

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌ 文件不存在: {input_path}")
        return

    with open(input_path, "r", encoding="utf-8") as f:
        export_data = json.load(f, object_hook=_json_hook)

    meta = export_data.get("_meta", {})
    print("=" * 50)
    print("  数据库导入")
    print(f"  源文件: {input_path}")
    print(f"  导出时间: {meta.get('exported_at', '未知')}")
    print(f"  源数据库: {meta.get('database', '未知')}")
    print(f"  集合数: {len(meta.get('collections', []))}")
    print("=" * 50)

    client, db_name = _build_target_client(args)
    db = client[db_name]
    print(f"  目标: {args.target_uri or '本地'}/{db_name}")

    total_imported = 0
    for col_name, docs in export_data.get("collections", {}).items():
        if not docs:
            print(f"  {col_name}: 0 条（跳过）")
            continue

        if args.drop_before_import:
            await db[col_name].drop()

        try:
            result = await db[col_name].insert_many(docs, ordered=False)
            count = len(result.inserted_ids)
        except Exception as e:
            err_msg = str(e)
            if "duplicate key" in err_msg.lower() or "E11000" in err_msg:
                from pymongo.errors import BulkWriteError
                if isinstance(e, BulkWriteError):
                    count = e.details.get("nInserted", 0)
                else:
                    count = 0
                print(f"  {col_name}: {count} 条新增（部分重复跳过）")
            else:
                print(f"  {col_name}: ❌ 错误 - {err_msg}")
                count = 0

        total_imported += count
        if count == len(docs):
            print(f"  {col_name}: {count} 条")

    print(f"\n✅ 导入完成: 共 {total_imported} 条")
    client.close()


async def cmd_clone(args):
    """直接从源 DB clone 全量数据到目标 DB（无需中间文件）"""
    settings = _source_settings()
    src_client = _get_source_client()
    src_db = src_client[settings.MONGODB_DATABASE]

    tgt_client = _get_target_client(
        args.target_uri,
        username=args.target_user,
        password=args.target_pass,
        auth_source=args.target_auth_source or "admin",
        direct=args.target_direct,
    )
    tgt_db_name = args.target_db or settings.MONGODB_DATABASE
    tgt_db = tgt_client[tgt_db_name]

    collections = await _list_all_collections(src_db)

    print("=" * 50)
    print("  数据库直接迁移（clone）")
    print(f"  源: {settings.MONGODB_URI}/{settings.MONGODB_DATABASE}")
    print(f"  目标: {args.target_uri}/{tgt_db_name}")
    print(f"  集合数: {len(collections)}")
    if args.drop_before_import:
        print("  ⚠️  模式: 清空目标后写入")
    print("=" * 50)

    total = 0
    for col_name in collections:
        docs = []
        async for doc in src_db[col_name].find():
            docs.append(doc)

        if not docs:
            print(f"  {col_name}: 0 条（跳过）")
            continue

        if args.drop_before_import:
            await tgt_db[col_name].drop()

        try:
            result = await tgt_db[col_name].insert_many(docs, ordered=False)
            count = len(result.inserted_ids)
        except Exception as e:
            err_msg = str(e)
            if "duplicate key" in err_msg.lower() or "E11000" in err_msg:
                from pymongo.errors import BulkWriteError
                if isinstance(e, BulkWriteError):
                    count = e.details.get("nInserted", 0)
                else:
                    count = 0
                print(f"  {col_name}: {count} 条新增（部分重复跳过）")
            else:
                print(f"  {col_name}: ❌ {err_msg}")
                count = 0

        total += count
        if count == len(docs):
            print(f"  {col_name}: {count} 条")

    print(f"\n✅ 迁移完成: 共 {total} 条")
    src_client.close()
    tgt_client.close()


def _add_target_args(p):
    """给子命令添加通用目标 DB 参数"""
    p.add_argument("--target-uri", help="目标 MongoDB URI（如 mongodb://host:27017）")
    p.add_argument("--target-user", help="目标 DB 用户名")
    p.add_argument("--target-pass", help="目标 DB 密码")
    p.add_argument("--target-db", help="目标数据库名")
    p.add_argument("--target-auth-source", default="admin", help="目标认证数据库（默认 admin）")
    p.add_argument("--target-direct", action="store_true", help="目标直连模式")
    p.add_argument("--drop-before-import", action="store_true", help="写入前清空目标集合（慎用）")


async def main():
    parser = argparse.ArgumentParser(description="数据库导出 & 迁移")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="导出全量数据库到 JSON")
    p_export.add_argument("--output", "-o", help="输出文件路径")
    p_export.add_argument("--collections", "-c", help="指定集合（逗号分隔，不指定则全量）")

    p_import = sub.add_parser("import", help="从 JSON 导入到目标数据库")
    p_import.add_argument("--input", "-i", required=True, help="JSON 文件路径")
    _add_target_args(p_import)

    p_clone = sub.add_parser("clone", help="直接从源 DB 全量迁移到目标 DB")
    _add_target_args(p_clone)

    args = parser.parse_args()

    if args.command == "export":
        await cmd_export(args)
    elif args.command == "import":
        await cmd_import(args)
    elif args.command == "clone":
        if not args.target_uri:
            print("❌ clone 模式必须指定 --target-uri")
            return
        await cmd_clone(args)


if __name__ == "__main__":
    asyncio.run(main())
