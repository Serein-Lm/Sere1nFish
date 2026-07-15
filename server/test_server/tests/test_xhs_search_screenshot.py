"""
XHS 综合测试 — 多关键词搜索翻页 + API详情风控 + 截屏兜底 + LLM分析对比

测试项:
1. 多关键词搜索翻页压测: 2个关键词各翻5页，探测406阈值，记录关键词切换间隔
2. API获取笔记详情: 10条，测风控延迟+重试
3. 方案A: API失败的用截屏兜底 + LLM分析
4. 方案B: 全部用截屏 + LLM分析
5. 时间/内存对比

用法:
    python test_server/tests/test_xhs_search_screenshot.py

日志: test_server/tests/logs/test_xhs.log
"""
import asyncio
import logging
import random
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "test_xhs.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8", mode="w"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("test_xhs")

COOKIE_FILE = Path(__file__).parent / "xhs_cookie.txt"
SEARCH_KEYWORDS = ["百度 实习", "百度 跳槽"]

# 提前 import 截屏相关模块（避免函数中间 import 失败）
from api.services.xhs_vision_tools import screenshot_note_detail, save_screenshots_to_files, analyze_note_screenshots_with_vision
from browser_manager.provider import get_browser_provider
SEARCH_PAGES = 5
PAGE_INTERVAL = (3, 6)
KEYWORD_INTERVAL = (5, 10)
DETAIL_COUNT = 10
API_MAX_RETRIES = 2
API_INTERVAL = (2, 5)
SCREENSHOT_CONCURRENCY = 4


def _mem() -> str:
    try:
        import docker
        client = docker.from_env()
        cs = client.containers.list(filters={"name": "chrome-"})
        if not cs:
            return "无容器"
        total = 0.0
        parts = []
        for c in cs:
            s = c.stats(stream=False)
            gb = s.get("memory_stats", {}).get("usage", 0) / 1024**3
            total += gb
            parts.append(f"{c.name}: {gb:.2f}G")
        return f"总计: {total:.2f}G | " + " | ".join(parts)
    except Exception as e:
        return f"获取失败: {e}"


async def main():
    logger.info("🚀 XHS 综合测试")
    cookie_string = ""
    if COOKIE_FILE.exists():
        cookie_string = COOKIE_FILE.read_text(encoding="utf-8").strip()
        logger.info(f"Cookie 已加载 ({len(cookie_string)} 字符)")
    else:
        logger.error("未找到 xhs_cookie.txt"); return

    from crawler_tools.xhs_crawler import create_crawler
    crawler = await create_crawler()
    login = await crawler.login_by_cookie_string(cookie_string)
    if not login.success:
        logger.error(f"登录失败: {login.message}"); return
    logger.info("登录成功")

    # ═══════════════════════════════════════
    # 测试 1: 多关键词搜索翻页
    # ═══════════════════════════════════════
    logger.info(f"\n{'='*60}")
    logger.info(f"测试 1: 多关键词搜索翻页")
    logger.info(f"  关键词: {SEARCH_KEYWORDS}")
    logger.info(f"  每词翻{SEARCH_PAGES}页 | 翻页间隔{PAGE_INTERVAL}s | 关键词切换间隔{KEYWORD_INTERVAL}s")
    logger.info(f"{'='*60}")

    all_notes = []
    search_timeline = []
    page_406_info = None
    t_search_start = time.time()
    last_req_end = t_search_start
    req_num = 0
    hit_406 = False

    for kw_idx, keyword in enumerate(SEARCH_KEYWORDS):
        if hit_406:
            break

        if kw_idx > 0:
            kw_gap = time.time() - last_req_end
            kw_wait = random.uniform(*KEYWORD_INTERVAL)
            logger.info(f"\n  ⏱️ 关键词切换 | 距上次请求: {kw_gap:.2f}s | 等待: {kw_wait:.2f}s")
            await asyncio.sleep(kw_wait)

        logger.info(f"\n  ── 关键词 [{kw_idx+1}/{len(SEARCH_KEYWORDS)}]: '{keyword}' ──")

        for page in range(1, SEARCH_PAGES + 1):
            req_num += 1
            gap = time.time() - last_req_end
            cumulative = time.time() - t_search_start
            logger.info(f"\n  [{keyword}] 第{page}/{SEARCH_PAGES}页 | 请求#{req_num} | 间隔={gap:.2f}s | 累计={cumulative:.1f}s")

            t0 = time.time()
            entry = {"keyword": keyword, "page": page, "req": req_num, "gap": gap}
            try:
                sr = await crawler.search_notes(keyword=keyword, page=page, page_size=20, sort_type="time_descending")
                elapsed = time.time() - t0
                last_req_end = time.time()
                entry["time"] = elapsed

                if sr.success:
                    entry["status"] = "ok"
                    entry["count"] = len(sr.items)
                    logger.info(f"  ✅ 成功 | 耗时={elapsed:.2f}s | 结果={len(sr.items)}条")
                    for note in sr.items:
                        all_notes.append({
                            "note_id": note.get("note_id", ""),
                            "xsec_token": note.get("xsec_token", ""),
                            "xsec_source": note.get("xsec_source", ""),
                            "title": note.get("title", "")[:30],
                            "keyword": keyword,
                        })
                else:
                    entry["status"] = str(sr.message)[:30]
                    entry["count"] = 0
                    logger.warning(f"  ❌ 失败 | 耗时={elapsed:.2f}s | {sr.message}")
                    if "406" in str(sr.message):
                        entry["status"] = "406"
                        page_406_info = f"{keyword} 第{page}页 (请求#{req_num}, 间隔={gap:.2f}s)"
                        logger.warning(f"  🚨 搜索406! {page_406_info}")
                        search_timeline.append(entry)
                        hit_406 = True
                        break
            except Exception as e:
                elapsed = time.time() - t0
                last_req_end = time.time()
                entry["time"] = elapsed
                entry["status"] = str(e)[:30]
                entry["count"] = 0
                logger.error(f"  ❌ 异常 | 耗时={elapsed:.2f}s | {e}")
                if "406" in str(e):
                    entry["status"] = "406"
                    page_406_info = f"{keyword} 第{page}页 (请求#{req_num}, 间隔={gap:.2f}s)"
                    search_timeline.append(entry)
                    hit_406 = True
                    break

            search_timeline.append(entry)
            wait = random.uniform(*PAGE_INTERVAL)
            logger.info(f"  ⏱️ 翻页间隔: {wait:.2f}s")
            await asyncio.sleep(wait)

    t_search_total = time.time() - t_search_start
    logger.info(f"\n搜索汇总: {len(all_notes)}条 | {req_num}次请求 | {t_search_total:.1f}s | 406={page_406_info or '未出现'}")

    logger.info(f"\n  搜索时间线:")
    logger.info(f"  {'#':>3} | {'关键词':>10} | {'页':>2} | {'间隔s':>7} | {'耗时s':>7} | {'状态':>8} | {'条数':>4}")
    logger.info(f"  {'-'*3}-+-{'-'*10}-+-{'-'*2}-+-{'-'*7}-+-{'-'*7}-+-{'-'*8}-+-{'-'*4}")
    for e in search_timeline:
        logger.info(f"  {e['req']:>3} | {e['keyword'][:10]:>10} | {e['page']:>2} | {e['gap']:>7.2f} | {e.get('time',0):>7.2f} | {e['status']:>8} | {e.get('count',''):>4}")

    test_notes = all_notes[:DETAIL_COUNT]
    if not test_notes:
        logger.error("无笔记可测试"); await crawler.close(); return
    logger.info(f"\n取前 {len(test_notes)} 条进行详情测试")

    # ═══════════════════════════════════════
    # 测试 2: API 获取笔记详情
    # ═══════════════════════════════════════
    logger.info(f"\n{'='*60}")
    logger.info(f"测试 2: API详情 ({len(test_notes)}条, 间隔{API_INTERVAL}s, 重试{API_MAX_RETRIES}次)")
    logger.info(f"{'='*60}")

    api_ok = {}
    api_fail = []
    detail_406_info = None
    detail_timeline = []
    t_api_start = time.time()
    last_api_end = t_api_start

    for idx, note in enumerate(test_notes, 1):
        nid = note["note_id"]
        gap = time.time() - last_api_end
        cumulative = time.time() - t_api_start
        logger.info(f"  [{idx}/{len(test_notes)}] note={nid} | 间隔={gap:.2f}s | 累计={cumulative:.1f}s")

        detail = None
        is_406 = False
        attempts = []

        for attempt in range(1, API_MAX_RETRIES + 1):
            t0 = time.time()
            try:
                detail = await crawler._client.get_note_by_id(
                    note_id=nid, xsec_source=note.get("xsec_source", ""), xsec_token=note.get("xsec_token", ""),
                )
                elapsed = time.time() - t0
                last_api_end = time.time()
                desc = (detail.get("desc", "") or "")[:60] if detail else ""
                logger.info(f"    ✅ 第{attempt}次 | 耗时={elapsed:.2f}s | desc={desc}")
                attempts.append({"attempt": attempt, "time": elapsed, "status": "ok"})
                break
            except Exception as e:
                elapsed = time.time() - t0
                last_api_end = time.time()
                err = str(e)[:80]
                logger.warning(f"    ❌ 第{attempt}次 | 耗时={elapsed:.2f}s | {err}")
                attempts.append({"attempt": attempt, "time": elapsed, "status": err[:30]})
                if "406" in str(e):
                    is_406 = True
                    if not detail_406_info:
                        detail_406_info = f"第{idx}条 (间隔={gap:.2f}s, 累计={cumulative:.1f}s)"
                        logger.warning(f"    🚨 详情406首次! {detail_406_info}")
                if attempt < API_MAX_RETRIES:
                    rw = random.uniform(5, 15) * attempt
                    logger.info(f"    ⏱️ 重试等待: {rw:.2f}s")
                    await asyncio.sleep(rw)

        detail_timeline.append({
            "idx": idx, "nid": nid, "gap": gap, "ok": detail is not None, "is_406": is_406, "attempts": attempts,
        })

        if detail:
            api_ok[nid] = (detail.get("desc", "") or "")[:200]
        else:
            api_fail.append(note)

        wait = random.uniform(*API_INTERVAL)
        logger.info(f"    ⏱️ 请求间隔: {wait:.2f}s")
        await asyncio.sleep(wait)

    t_api_total = time.time() - t_api_start
    await crawler.close()

    logger.info(f"\nAPI汇总: 成功={len(api_ok)} 失败={len(api_fail)} 耗时={t_api_total:.1f}s | 406={detail_406_info or '未出现'}")

    logger.info(f"\n  API时间线:")
    logger.info(f"  {'#':>3} | {'间隔s':>7} | {'总耗时s':>8} | {'重试':>4} | {'状态':>4} | {'406':>3}")
    logger.info(f"  {'-'*3}-+-{'-'*7}-+-{'-'*8}-+-{'-'*4}-+-{'-'*4}-+-{'-'*3}")
    for e in detail_timeline:
        t_total = sum(a["time"] for a in e["attempts"])
        st = "✅" if e["ok"] else "❌"
        f = "是" if e["is_406"] else ""
        logger.info(f"  {e['idx']:>3} | {e['gap']:>7.2f} | {t_total:>8.2f} | {len(e['attempts']):>4} | {st:>4} | {f:>3}")

    # ═══════════════════════════════════════
    # 测试 3: 方案A — 截屏兜底 + LLM
    # ═══════════════════════════════════════
    provider = get_browser_provider()
    out_dir = str(_root / "data" / "test_xhs_screenshots")
    t_fallback = 0
    fallback_results = {}

    if api_fail:
        logger.info(f"\n{'='*60}")
        logger.info(f"测试 3: 方案A — 截屏兜底 ({len(api_fail)}条) + LLM")
        logger.info(f"{'='*60}")
        tid_a = "test-fallback"
        cdp_a = await provider.get_cdp_endpoint(task_id=tid_a, purpose="xhs_screenshot")
        if cdp_a:
            logger.info(f"内存(开始): {_mem()}")
            sem = asyncio.Semaphore(SCREENSHOT_CONCURRENCY)

            async def _fb(note, i):
                async with sem:
                    nid = note["note_id"]
                    t0 = time.time()
                    r = await screenshot_note_detail(nid, note.get("xsec_token",""), cookie_string=cookie_string, cdp_endpoint=cdp_a)
                    t_ss = time.time() - t0
                    cnt = len(r.get("screenshots", []))
                    logger.info(f"  [{i+1}] {nid[:12]}... 截图={cnt} 截屏={t_ss:.1f}s")
                    analysis = ""; t_llm = 0
                    if r.get("screenshots"):
                        await save_screenshots_to_files(r["screenshots"], f"fb_{nid}", output_dir=out_dir)
                        t1 = time.time()
                        analysis = analyze_note_screenshots_with_vision(r["screenshots"])
                        t_llm = time.time() - t1
                        logger.info(f"    LLM={t_llm:.1f}s | {analysis[:100]}")
                    fallback_results[nid] = {"ss": cnt, "ss_t": t_ss, "llm_t": t_llm, "len": len(analysis)}

            t0 = time.time()
            await asyncio.gather(*[_fb(n, i) for i, n in enumerate(api_fail)])
            t_fallback = time.time() - t0
            logger.info(f"内存(兜底后): {_mem()}")
            await provider.release_cdp_endpoint(task_id=tid_a)
            logger.info(f"方案A兜底耗时: {t_fallback:.1f}s")
    else:
        logger.info("\n所有API成功，无需兜底")

    # ═══════════════════════════════════════
    # 测试 4: 方案B — 全部截屏 + LLM
    # ═══════════════════════════════════════
    logger.info(f"\n{'='*60}")
    logger.info(f"测试 4: 方案B — 全部截屏 ({len(test_notes)}条) + LLM")
    logger.info(f"{'='*60}")
    tid_b = "test-allss"
    cdp_b = await provider.get_cdp_endpoint(task_id=tid_b, purpose="xhs_screenshot")
    t_all_ss = 0
    all_ss_results = {}
    if cdp_b:
        logger.info(f"内存(开始): {_mem()}")
        sem = asyncio.Semaphore(SCREENSHOT_CONCURRENCY)

        async def _allss(note, i):
            async with sem:
                nid = note["note_id"]
                t0 = time.time()
                r = await screenshot_note_detail(nid, note.get("xsec_token",""), cookie_string=cookie_string, cdp_endpoint=cdp_b)
                t_ss = time.time() - t0
                cnt = len(r.get("screenshots", []))
                logger.info(f"  [{i+1}] {nid[:12]}... 截图={cnt} 截屏={t_ss:.1f}s")
                analysis = ""; t_llm = 0
                if r.get("screenshots"):
                    await save_screenshots_to_files(r["screenshots"], f"all_{nid}", output_dir=out_dir)
                    t1 = time.time()
                    analysis = analyze_note_screenshots_with_vision(r["screenshots"])
                    t_llm = time.time() - t1
                    logger.info(f"    LLM={t_llm:.1f}s | {analysis[:100]}")
                all_ss_results[nid] = {"ss": cnt, "ss_t": t_ss, "llm_t": t_llm, "len": len(analysis)}

        t0 = time.time()
        await asyncio.gather(*[_allss(n, i) for i, n in enumerate(test_notes)])
        t_all_ss = time.time() - t0
        logger.info(f"内存(全截屏后): {_mem()}")
        await provider.release_cdp_endpoint(task_id=tid_b)
        logger.info(f"方案B总耗时: {t_all_ss:.1f}s")

    # ═══════════════════════════════════════
    # 汇总
    # ═══════════════════════════════════════
    logger.info(f"\n{'='*60}")
    logger.info(f"汇总对比")
    logger.info(f"{'='*60}")
    logger.info(f"搜索: {len(all_notes)}条 | {req_num}次请求 | {t_search_total:.1f}s | 406={page_406_info or '未出现'}")
    logger.info(f"API详情: 成功={len(api_ok)} 失败={len(api_fail)} | {t_api_total:.1f}s | 406={detail_406_info or '未出现'}")
    logger.info(f"")
    logger.info(f"方案A (API优先+截屏兜底):")
    logger.info(f"  API: {t_api_total:.1f}s ({len(api_ok)}条成功)")
    if fallback_results:
        logger.info(f"  兜底: {t_fallback:.1f}s ({len(api_fail)}条)")
    logger.info(f"  总计: {t_api_total + t_fallback:.1f}s")
    logger.info(f"")
    if all_ss_results:
        ss_sum = sum(r["ss_t"] for r in all_ss_results.values())
        llm_sum = sum(r["llm_t"] for r in all_ss_results.values())
        logger.info(f"方案B (全截屏+LLM):")
        logger.info(f"  截屏串行预估: {ss_sum:.1f}s | 实际并发: {t_all_ss:.1f}s")
        logger.info(f"  LLM串行预估: {llm_sum:.1f}s")
        logger.info(f"  总计: {t_all_ss:.1f}s")
    logger.info(f"")
    logger.info(f"内存(最终): {_mem()}")
    logger.info(f"日志: {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
