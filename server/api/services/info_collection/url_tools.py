"""URL/web information collection tool adapters."""

from __future__ import annotations

import copy
import time
from typing import Any, Callable

from api.dao import web_tagging as web_tagging_dao
from api.services.info_collection.contracts import (
    ProbeRequest,
    ProbeResult,
    ScanRequest,
    ScanResult,
    SearchRequest,
    SearchResult,
)
from core.logger import get_logger


logger = get_logger("api.services.info_collection.url_tools")


def _normalize_probe_item(item: Any) -> dict[str, Any]:
    """Normalize hunter/probe result models into plain dictionaries."""
    if isinstance(item, dict):
        return dict(item)
    return {
        "url": getattr(item, "url", ""),
        "status_code": getattr(item, "status_code", None),
        "title": getattr(item, "title", ""),
        "response_time": getattr(item, "response_time", None),
    }


class UrlProbeTool:
    """Probe a batch of URLs through the hunter runtime boundary."""

    name = "url_probe"

    def __init__(self, *, probe_func: Callable[..., Any] | None = None) -> None:
        self._probe_func = probe_func

    async def _probe_urls_batch(self, **kwargs: Any) -> Any:
        if self._probe_func:
            return await self._probe_func(**kwargs)
        from crawler_tools.hunter_tools import probe_urls_batch

        return await probe_urls_batch(**kwargs)

    async def probe(self, request: ProbeRequest) -> ProbeResult:
        logger.info(
            f"[probe] 开始探活 {len(request.urls)} 个 URL, "
            f"并发={request.concurrency}, 超时={request.timeout}s"
        )
        started = time.time()
        raw_results = await self._probe_urls_batch(
            urls=request.urls,
            concurrency=request.concurrency,
            timeout=request.timeout,
            only_alive=request.only_alive,
        )
        items = [_normalize_probe_item(item) for item in raw_results]
        elapsed = time.time() - started
        logger.info(f"[probe] 探活完成 ({elapsed:.1f}s), 存活={len(items)}/{len(request.urls)}")
        return ProbeResult(
            source=request.source,
            items=items,
            meta={
                "task_id": request.task_id,
                "project_id": request.project_id,
                "elapsed_seconds": elapsed,
                "total_urls": len(request.urls),
                "concurrency": request.concurrency,
                "timeout": request.timeout,
                "only_alive": request.only_alive,
            },
        )


class HunterSearchProbeTool:
    """Run Hunter discovery and liveness probing behind a search contract."""

    name = "hunter_search_probe"

    def __init__(self, *, search_func: Callable[..., Any] | None = None) -> None:
        self._search_func = search_func

    async def _search_and_probe(self, **kwargs: Any) -> Any:
        if self._search_func:
            return await self._search_func(**kwargs)
        from crawler_tools.hunter_tools import search_and_probe

        return await search_and_probe(**kwargs)

    async def search(self, request: SearchRequest) -> SearchResult:
        search_type = str(request.options.get("search_type", "icp"))
        probe_concurrency = int(request.options.get("probe_concurrency", 20))
        probe_timeout = float(request.options.get("probe_timeout", 10.0))
        raw_results = await self._search_and_probe(
            query=request.query,
            search_type=search_type,
            size=request.limit,
            probe_concurrency=probe_concurrency,
            probe_timeout=probe_timeout,
        )
        items = [_normalize_probe_item(item) for item in raw_results]
        return SearchResult(
            source=request.source,
            query=request.query,
            items=items,
            meta={
                "task_id": request.task_id,
                "project_id": request.project_id,
                "search_type": search_type,
                "limit": request.limit,
                "probe_concurrency": probe_concurrency,
                "probe_timeout": probe_timeout,
            },
        )


def _build_worker_chrome_config(app_config: Any, ws_url: str) -> Any:
    """Build an MCP config copy pointing chrome-devtools to one CDP endpoint."""
    config = copy.deepcopy(app_config)
    mcp_servers = config.mcp_servers or {}
    if "chrome-devtools" in mcp_servers:
        cfg = mcp_servers["chrome-devtools"]
        cleaned = []
        skip_next = False
        for arg in (cfg.args or []):
            if skip_next:
                skip_next = False
                continue
            if arg == "--browserUrl":
                skip_next = True
                continue
            if arg.startswith("--wsEndpoint"):
                continue
            cleaned.append(arg)
        cleaned.append(f"--wsEndpoint={ws_url}")
        cfg.args = cleaned
    return config


class UrlWebScanTool:
    """Scan one website URL through the web-tagging agent runtime."""

    name = "url_web_scan"

    def __init__(
        self,
        *,
        app_config: Any,
        db: Any,
        prompt_loader: Callable[[str], str] | None = None,
    ) -> None:
        self._app_config = app_config
        self._db = db
        self._prompt_loader = prompt_loader
        self._prompt: str | None = None

    def _get_prompt(self) -> str:
        if self._prompt is None:
            if self._prompt_loader:
                self._prompt = self._prompt_loader("web_tagging/web_tagging")
            else:
                from Sere1nGraph.graph.prompts.loader import load_prompt

                self._prompt = load_prompt("web_tagging/web_tagging")
        return self._prompt

    async def scan(self, request: ScanRequest) -> ScanResult:
        from browser_manager.provider import get_browser_provider
        from langchain_core.messages import HumanMessage
        from Sere1nGraph.graph.agents.factory import create_web_tagging_agent
        from Sere1nGraph.graph.agents.runtime import extract_with_retry

        url = request.target or request.target_info.get("url", "")
        worker_id = request.options.get("worker_id", 0)
        pipeline_id = request.options.get("pipeline_id", "")
        item_id = request.options.get("item_id", "")
        attempt = request.options.get("attempt", 0)
        url_task_id = f"url_scan_w{worker_id}_{pipeline_id}_{item_id}"

        provider = get_browser_provider()
        cdp_url = await provider.get_cdp_endpoint(task_id=url_task_id, purpose="url_scan")
        if not cdp_url:
            raise RuntimeError(f"无法获取 Chrome 容器 (url={url})")

        try:
            logger.info(
                f"[scan-w{worker_id}] 扫描 {url} (attempt={attempt}) | 容器={cdp_url}"
            )
            started = time.time()
            worker_config = _build_worker_chrome_config(self._app_config, cdp_url)
            agent = await create_web_tagging_agent(worker_config, streaming=False)
            raw = await agent({"messages": [HumanMessage(content=f"请分析以下 URL：{url}")]})
            tagging = await extract_with_retry(
                raw,
                worker_config,
                system_prompt=self._get_prompt(),
            )
            if not tagging:
                raise RuntimeError(f"agent 输出解析失败 (url={url})")

            elapsed = time.time() - started
            findings_count = len(tagging.get("findings", []))
            logger.info(f"[scan-w{worker_id}] ✓ {url} ({elapsed:.1f}s) findings={findings_count}")
            try:
                await web_tagging_dao.insert_web_tagging_result(
                    self._db,
                    request.project_id,
                    url,
                    tagging,
                    task_id=request.task_id,
                )
            except Exception as store_err:
                logger.warning(f"[scan-w{worker_id}] 存储失败: {store_err}")

            return ScanResult(
                source=request.source,
                target=url,
                success=True,
                data=tagging,
                raw=raw,
                meta={"elapsed_seconds": elapsed, "findings_count": findings_count},
            )
        finally:
            try:
                await provider.release_cdp_endpoint(task_id=url_task_id)
            except Exception:
                pass
