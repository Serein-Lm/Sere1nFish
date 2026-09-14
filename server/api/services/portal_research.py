"""Portal research policy, durable read context and evidence finalization.

Reuses Target research execution, SourceDocument archival and company scan
scheduling; no independent browser or task lifecycle is introduced.
"""
from __future__ import annotations
import asyncio
import json
import re
import time
from datetime import datetime, timezone

from api.dao import portal_research as dao
from api.models.portal_research import PortalResearchOptions
from api.services.portal_link_policy import CHILD_MARKERS, PARENT_MARKERS, canonical_link
from api.services.source_documents.service import ingest_source_url
from core.observability import observation_context
from Sere1nGraph.graph.agents.portal_research import PortalBrowserPolicy

CATEGORIES = ("business", "recruitment", "procurement", "investment", "feedback", "organization")

class PortalResearchSession:
    def __init__(self, db, task_id, project_id, target, options=None):
        self.db, self.task_id, self.project_id, self.target = db, task_id, project_id, target
        self.options = PortalResearchOptions.model_validate(options) if options is not None else None
        self.pages, self.saved, self.candidates = {}, set(), []
        self.deadline = time.monotonic() + (self.options.max_runtime_seconds if self.options else 900)
        self.browser = PortalBrowserPolicy(list(target.get("official_root_domains") or target.get("root_domains") or []), self.options) if self.options else None

    async def prepare(self):
        if not self.options: return
        checkpoint, self.candidates = await asyncio.gather(
            dao.load_checkpoint(self.db, self.task_id),
            dao.source_candidates(self.db, self.project_id, self.target["target_id"]),
        )
        if not self.browser.roots:
            raise ValueError("Target 暂无已核验的官网域名，请先执行机构资料核验")
        self.pages = {page["url"]: page for page in (checkpoint.get("pages") or {}).values() if page.get("url")}
        self.saved = set(self.pages)
        for page in self.pages.values():
            self.browser.observe_page(page)
            self.browser.visits[page["url"]] = 1

    def remaining_seconds(self):
        remaining = self.deadline - time.monotonic()
        if remaining < 5: raise TimeoutError("门户深研已达到本轮时间预算；阅读账本已保留")
        return remaining

    async def run_agent(self, agent, state):
        if not self.options:
            return await agent(state)
        async with asyncio.timeout(self.remaining_seconds()):
            return await agent(state)

    def query(self, original):
        if not self.options: return original
        settings = self.options.model_dump(exclude={"dry_run"})
        read = [{"url": page["url"], "title": page.get("title"), "text": page.get("text", "")[:1500]} for page in self.pages.values()]
        from Sere1nGraph.graph.agents.runtime import REQUIRE_EVIDENCE_TOOL_MARKER
        return (REQUIRE_EVIDENCE_TOOL_MARKER + "\n" + original.split("目标：", 1)[0]
            + "任务：深入研究已核验官网的六类栏目，遵循门户研究专用 Prompt。\n"
            + "范围与预算：" + json.dumps(settings, ensure_ascii=False)
            + "\n既有来源候选（仅导航线索）：" + json.dumps(self.candidates[:80], ensure_ascii=False)[:16000]
            + "\n恢复的真实阅读账本：" + json.dumps(read, ensure_ascii=False)[:30000])

    def prompt(self, fallback):
        if not self.options: return fallback
        from Sere1nGraph.graph.prompts.loader import load_prompt
        return load_prompt("target_research/portal_research")

    def observer(self, base):
        if not self.options: return base
        async def observe(name, result):
            base(name, result)
            if name not in {"evaluate_script", "evaluate"}: return
            from api.services.target_research import _browser_tool_text, _ERROR_PAGE_TITLE_RE
            text = _browser_tool_text(result)
            decoder = json.JSONDecoder()
            for offset, char in enumerate(text):
                if char != "{": continue
                try: page, _ = decoder.raw_decode(text[offset:])
                except ValueError: continue
                if not isinstance(page, dict) or not canonical_link(str(page.get("url") or "")): continue
                page["url"] = canonical_link(page["url"])
                if not page.get("text") or _ERROR_PAGE_TITLE_RE.search(str(page.get("title") or "")): return
                if len(self.pages) >= self.options.max_pages and page["url"] not in self.pages: return
                page = {"url":page["url"], "title":str(page.get("title") or "")[:500], "text":str(page["text"])[:12000], "links":list(page.get("links") or [])[:100], "read_at":datetime.now(timezone.utc).isoformat()}
                self.browser.observe_page(page)
                self.pages[page["url"]] = page
                if not self.options.dry_run:
                    await dao.save_page(self.db, self.task_id, page)
                    self.saved.add(page["url"])
                    from api.services.task_progress import update_task_stage
                    await update_task_stage(self.db, task_id=self.task_id, stage="portal_research", message=f"官网门户深研：已读取 {len(self.pages)} 页，正在补充栏目、业务与直属关系")
                return
        return observe

    async def flush(self):
        if not self.options or self.options.dry_run: return
        await asyncio.gather(*(dao.save_page(self.db, self.task_id, page) for url, page in self.pages.items() if url not in self.saved))

    def restrict(self, data):
        if not self.options: return data
        sources = {item["url"] for item in data.get("sources") or []}
        sections = {item["category"]: item for item in data.get("portal_sections") or []}
        data["portal_sections"] = []
        for category in CATEGORIES:
            section = sections.get(category) or {"category":category,"summary":"", "status":"not_found", "gaps":["本轮尚未形成栏目证据"]}
            section["source_urls"] = [url for url in section.get("source_urls") or [] if url in sources and url not in self.browser.blocked]
            if not section["source_urls"] and section.get("status") == "covered": section["status"] = "partial"
            data["portal_sections"].append(section)
        for item in data.get("related_targets") or []:
            parent = item.get("relation_type") == "parent_organization"
            enabled = self.options.include_parent if parent else self.options.include_subordinates
            marker = PARENT_MARKERS if parent else CHILD_MARKERS
            evidence = [self.pages.get(url, {}).get("text", "") for url in item.get("source_urls") or [] if url not in self.browser.blocked]
            # A section/menu link alone cannot establish a direct entity relation.
            name = item.get("name", "")
            proven = bool(name) and name not in self.browser.blocked_names and any(
                marker.search(text[max(0, match.start()-120):match.end()+120])
                for text in evidence for match in re.finditer(re.escape(name), text)
            )
            if not enabled or not proven or item.get("relation_type") in {"affiliated_unit", "partner", "vendor", "other"}:
                item["should_scan"] = False
        data["portal_options"] = self.options.model_dump()
        data["portal_page_count"] = len(self.pages)
        data["portal_excluded_link_count"] = len(self.browser.blocked)
        return data

    async def archive(self, data):
        if not self.options or self.options.dry_run: return data
        gate = asyncio.Semaphore(2)
        async def archive_source(source):
            async with gate:
                try:
                    with observation_context(project_id=self.project_id, task_id=self.task_id, phase="portal_archive", agent="source_document", task_type="target_research"):
                        timeout = min(180, self.remaining_seconds())
                        result = await asyncio.wait_for(ingest_source_url(self.db, url=source["url"], project_id=self.project_id, target=self.target, task_def_id=self.task_id, run_task_id=self.task_id, keyword="官网门户深研", discovery_context={"source":"official_portal_research"}), timeout=timeout)
                    source.update({"source_document_id": result.get("document_id"), "source_document_version_id":result.get("version_id"), "archive_status": result.get("archive_status") or result.get("status")})
                except Exception as exc:
                    source["archive_status"] = "pending"
                    source["archive_error"] = str(exc)[:500]
        await asyncio.gather(*(archive_source(item) for item in data.get("sources") or []))
        data["portal_archive_pending"] = sum(not item.get("source_document_version_id") or item.get("archive_status") in {"pending", "partial", "error"} for item in data.get("sources") or [])
        await dao.save_result(self.db, self.task_id, data)
        return data

    def scan_params(self, params):
        if not self.options: return params
        return {**dict(params or {}), "enable_asset_discovery":False, "enable_url_scan":True, "website_collection_mode":"deep", "enable_xhs":False, "enable_wechat":False, "enable_bidding":False, "enable_scholar":False, "enable_copywriting":False, "enable_control_structure":False}
