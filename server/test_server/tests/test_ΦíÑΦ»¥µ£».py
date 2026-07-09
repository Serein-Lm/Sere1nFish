"""
补话术脚本 — 查找项目下所有没有话术的 findings（score>=60），并发生成

运行：python test_server/tests/test_补话术.py
"""

import asyncio
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

LOG_FILE = _root / "logs" / "test_补话术.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

root = logging.getLogger()
root.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
fh = logging.FileHandler(str(LOG_FILE), mode="w", encoding="utf-8")
fh.setLevel(logging.DEBUG)
fh.setFormatter(fmt)
root.addHandler(fh)
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(fmt)
root.addHandler(ch)
logger = logging.getLogger("补话术")

PROJECT_ID = "69c8e71138c689f2defb7e0d"
NUM_WORKERS = 5


async def main():
    from Sere1nGraph.graph.config.loader import load_config
    from api.db.mongodb import init_mongo, get_db
    from api.dao import findings as findings_dao
    from api.services.url_scan_pipeline import UrlScanPipeline

    app_config = load_config()
    init_mongo(app_config)
    db = get_db()

    logger.info(f"项目: {PROJECT_ID} | 并发: {NUM_WORKERS}")

    # 查所有 source=web_tagging 且 score>=60 的 findings
    all_findings, total = await findings_dao.query_findings(
        db, project_id=PROJECT_ID, source="web_tagging", min_score=60, limit=500, sort="score_desc",
    )
    logger.info(f"web_tagging source, score>=60 的 findings: {total}")

    # 查已有话术
    existing = set()
    async for doc in db["copywritings"].find({"project_id": PROJECT_ID}, {"finding_id": 1}):
        existing.add(doc.get("finding_id", ""))
    logger.info(f"已有话术: {len(existing)}")

    need = [f for f in all_findings if f.get("finding_id") not in existing]
    logger.info(f"待生成: {len(need)}")

    if not need:
        logger.info("全部已有话术，退出")
        return

    by_url = defaultdict(list)
    for f in need:
        by_url[f.get("url", "")].append(f)

    queue: asyncio.Queue = asyncio.Queue()
    for f in need:
        queue.put_nowait(f)

    pipeline = UrlScanPipeline(db, app_config)
    success = 0
    fail = 0
    t0 = time.time()

    async def worker(wid):
        nonlocal success, fail
        while not queue.empty():
            try:
                finding = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            fid = finding.get("finding_id", "?")
            score = finding.get("attention_score", 0)
            label = finding.get("label", "")[:30]
            url = finding.get("url", "")

            site_ctx = {
                "url": url,
                "domain": finding.get("domain", ""),
                "site_name": finding.get("site_name"),
                "entity_name": finding.get("entity_name"),
                "summary": finding.get("summary"),
            }
            siblings = [f for f in by_url.get(url, []) if f.get("finding_id") != fid]

            logger.info(f"[w{wid}] {fid} | score={score} | {label} | 剩余={queue.qsize()}")
            t1 = time.time()
            try:
                cw = await pipeline.generate_copywriting_for_finding(finding, site_ctx, siblings)
                cw["project_id"] = PROJECT_ID
                cw["task_id"] = finding.get("task_id", "")
                await findings_dao.insert_copywriting(db, {**cw, "source": finding.get("source", "web_tagging")})
                success += 1
                logger.info(f"[w{wid}] ✓ ({time.time()-t1:.1f}s) | {fid} | 已完成={success}")
            except Exception as e:
                fail += 1
                logger.error(f"[w{wid}] ✗ ({time.time()-t1:.1f}s) | {fid}: {e}")

    workers = [asyncio.create_task(worker(i)) for i in range(NUM_WORKERS)]
    await asyncio.gather(*workers, return_exceptions=True)

    logger.info(f"完成 | 总耗时={time.time()-t0:.1f}s | 成功={success} | 失败={fail} | 日志: {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
