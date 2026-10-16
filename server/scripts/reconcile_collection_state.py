"""Bounded maintenance: inspect legacy states, save BSON before-images, then CAS.

Use a separate Compose run container with an explicit output volume.
Default is read-only; --apply writes only the reviewed repair categories.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import uuid

from bson import json_util


async def reconcile(args):
    from api.db.mongodb import init_mongo, get_db, close_mongo
    from api.storage import get_object_storage
    from api.dao.collection_reconciliation import apply_action, backup_documents
    from api.services.collection_reconciliation import build_reconciliation_plan, public_plan
    init_mongo()
    try:
        db, storage = get_db(), await get_object_storage()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=args.older_than_hours)
        plan = await build_reconciliation_plan(db, storage, cutoff=cutoff)
        plan_id = uuid.uuid4().hex
        output = args.output.resolve() / plan_id
        output.mkdir(parents=True, mode=0o700)
        (output / "plan.json").write_text(json_util.dumps(public_plan(plan), indent=2, ensure_ascii=False))
        outcomes = []
        if args.apply:
            # The lossless before-image file is durable before the first mutation.
            with (output / "before.jsonl").open("w") as stream:
                async for record in backup_documents(db, plan["actions"]):
                    stream.write(json_util.dumps(record, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            for action in plan["actions"]:
                outcome = await apply_action(db, action, plan_id=plan_id)
                outcomes.append({"id": action["id"], "kind": action["kind"], **outcome})
                (output / "outcomes.json").write_text(json.dumps(outcomes, indent=2))
        report = {"plan_id": plan_id, "apply": args.apply, "counts": dict(Counter(a["kind"] for a in plan["actions"])),
                  "skipped": plan["skipped"], "applied": sum(bool(o.get("applied")) for o in outcomes),
                  "pages_requeued": sum(o.get("pages_requeued", 0) for o in outcomes), "output": str(output)}
        (output / "summary.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    finally:
        close_mongo()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--older-than-hours", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.older_than_hours < 1 or args.timeout < 1:
        parser.error("age and timeout must be positive")
    os.umask(0o077)
    async def run():
        await asyncio.wait_for(reconcile(args), args.timeout)
    asyncio.run(run())


if __name__ == "__main__":
    main()
