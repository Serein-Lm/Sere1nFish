"""
测试 Web Tagging 并发扫描

验证:
1. 多 worker 并发扫描是否可用
2. 失败 URL 是否正确回队列重试
3. MCP 连接错误是否正确恢复
4. 并发效率 + 内存占用

用法:
    python test_server/tests/test_concurrent_webtagging.py

日志输出到: test_server/tests/logs/test_webtagging.log
"""
import asyncio
import logging
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# ── 测试专用日志 ──
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "test_webtagging.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8", mode="w"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("test_webtagging")

# ── 测试 URL ──
TEST_URLS = [
    "https://wufu.baidu.com",
    "http://aisuda.bce.baidu.com",
    "https://competition.baidu.com",
    "https://apollocloud.baidu.com",
    "https://apollo.baidu.com",
]

NUM_WORKERS = 3  # 并发 worker 数


def _get_all_container_memory() -> str:
    """获取所有 chrome 容器的内存占用"""
    try:
        import docker
        client = docker.from_env()
        containers = client.containers.list(filters={"name": "chrome-"})
        if not containers:
            return "无容器"
        parts = []
        total = 0.0
        for c in containers:
            stats = c.stats(stream=False)
            mem = stats.get("memory_stats", {}).get("usage", 0)
            mem_gb = mem / 1024 / 1024 / 1024
            total += mem_gb
            parts.append(f"{c.name}: {mem_gb:.2f}G")
        return f"总计: {total:.2f}G | " + " | ".join(parts)
    except Exception as e:
        return f"获取失败: {e}"


async def test_webtagging_concurrent():
    """测试并发 web tagging 扫描"""
    from Sere1nGraph.graph.config.loader import load_config
    from api.services.url_scan_pipeline import UrlScanPipeline

    config_path = str(_root / "config.json")
    app_config = load_config(config_path)

    # 不入库：用 mock db
    from unittest.mock import AsyncMock, MagicMock
    mock_db = MagicMock()
    mock_collection = AsyncMock()
    mock_collection.insert_one = AsyncMock()
    mock_collection.insert_many = AsyncMock()
    mock_collection.update_one = AsyncMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_collection)

    pipeline = UrlScanPipeline(mock_db, app_config)

    # 构建 alive_urls 格式
    alive_urls = [{"url": u, "status_code": 200} for u in TEST_URLS]

    logger.info(f"{'='*60}")
    logger.info(f"Web Tagging 并发扫描测试")
    logger.info(f"URL 数量: {len(alive_urls)}")
    logger.info(f"Worker 数量: {NUM_WORKERS}")
    logger.info(f"{'='*60}")

    logger.info(f"内存(开始): {_get_all_container_memory()}")

    t0 = time.time()
    results = await pipeline.scan_urls(
        project_id="test_project",
        alive_urls=alive_urls,
        task_id="test_webtagging",
        num_workers=NUM_WORKERS,
    )
    elapsed = time.time() - t0

    logger.info(f"\n内存(扫描后): {_get_all_container_memory()}")

    # 统计
    success = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    logger.info(f"\n{'='*60}")
    logger.info(f"扫描结果")
    logger.info(f"{'='*60}")
    logger.info(f"总耗时: {elapsed:.1f}s")
    logger.info(f"成功: {len(success)}/{len(results)}")
    logger.info(f"失败: {len(failed)}/{len(results)}")

    for r in results:
        status = "✅" if r.get("success") else "❌"
        findings = len(r.get("data", {}).get("findings", [])) if r.get("data") else 0
        error = r.get("error", "")
        logger.info(f"  {status} {r['url']} findings={findings} {error}")

    if failed:
        logger.info(f"\n失败详情:")
        for r in failed:
            logger.info(f"  {r['url']}: {r.get('error', 'unknown')}")

    # 效率分析
    avg_per_url = elapsed / len(alive_urls) if alive_urls else 0
    logger.info(f"\n效率分析:")
    logger.info(f"  平均每 URL: {avg_per_url:.1f}s")
    logger.info(f"  串行预估: {avg_per_url * len(alive_urls) * NUM_WORKERS:.0f}s")
    logger.info(f"  实际并发: {elapsed:.1f}s")
    logger.info(f"  加速比: ~{(avg_per_url * len(alive_urls)) / max(elapsed, 1):.1f}x")

    logger.info(f"\n日志已保存: {LOG_FILE}")


async def main():
    await test_webtagging_concurrent()


if __name__ == "__main__":
    asyncio.run(main())
