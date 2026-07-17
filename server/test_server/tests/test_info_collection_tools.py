import asyncio
import logging

from api.db.collections import COPYWRITINGS_COLLECTION
from api.services.company_scan_pipeline import (
    PROFILE_COPYWRITINGS_COLLECTION,
    _ProfileCopywritingStage,
)
from api.services.url_scan_pipeline import _CopywritingStage as _UrlCopywritingStage
from api.services.url_scan_pipeline import _UrlScanStage, UrlScanPipeline
from api.services.info_collection import (
    CopywritingRequest,
    CopywritingResult,
    DetailRequest,
    DetailResult,
    InfoCollectionToolFactory,
    ProfileRequest,
    ProbeRequest,
    ProbeResult,
    ScanResult,
    SearchRequest,
    SearchResult,
    make_stream_items,
    run_stream_pipeline,
    stream_stage,
    TagResult,
    XhsDetailStage,
    XhsNoteTaggingPersistStage,
    XhsPrefetchedDetailTaggingStage,
    XhsSearchStage,
    XhsTaggingStage,
)
from api.services.info_collection.copywriting_tools import AgentCopywritingTool
from api.services.info_collection.url_tools import HunterSearchProbeTool, UrlProbeTool
from api.services.info_collection.xhs_tools import XhsDetailTool, XhsProfileTool, XhsSearchTool


class _FakeSearchTool:
    def __init__(self) -> None:
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        return SearchResult(
            source="xhs",
            query=request.query,
            items=[
                {
                    "note_id": "note-1",
                    "title": "hello",
                    "task_id": request.task_id,
                    "project_id": request.project_id,
                }
            ],
        )


class _FakeDetailTool:
    def __init__(self) -> None:
        self.requests = []

    async def fetch_detail(self, request):
        self.requests.append(request)
        return DetailResult(
            source="xhs",
            item_id=request.item_id,
            content="detail-content",
            raw={"desc": "detail-content"},
            comments_summary="comment-summary",
            images_urls=["https://img.example/a.jpg"],
        )


class _FakeTaggingTool:
    def __init__(self, kind: str, score: int = 80, findings: list[dict] | None = None) -> None:
        self.kind = kind
        self.score = score
        self.findings = findings or []
        self.requests = []

    async def tag(self, request):
        self.requests.append(request)
        return TagResult(
            source="xhs",
            kind=self.kind,
            item_id=request.item_id,
            tagging={
                "attention_score": self.score,
                "summary": "detail-summary",
                "findings": list(self.findings),
            },
        )


class _FakeCopywritingTool:
    def __init__(self) -> None:
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return CopywritingResult(
            source=request.source,
            project_id=request.project_id,
            task_id=request.task_id,
            target_id=request.target_id,
            copywritings=[{"finding_id": "profile-user-1", "url": request.options["url"]}],
        )


class _FakeScanTool:
    def __init__(self) -> None:
        self.requests = []

    async def scan(self, request):
        self.requests.append(request)
        return ScanResult(
            source=request.source,
            target=request.target,
            success=True,
            data={"findings": [{"label": "HR 邮箱"}]},
        )


class _FakeContext:
    def __init__(self, state) -> None:
        self.state = state
        self.logger = logging.getLogger("test_info_collection_tools")
        self.worker_id = 0
        self.pipeline = type("Pipeline", (), {"pipeline_id": "pipe-1"})()
        self.emitted = []

    async def emit(self, stage, payload):
        self.emitted.append((stage, payload))


class _FakeInsertResult:
    inserted_id = "inserted"


class _FakeInsertManyResult:
    def __init__(self, count: int) -> None:
        self.inserted_ids = [f"inserted-{idx}" for idx in range(count)]


class _FakeCollection:
    def __init__(self) -> None:
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return _FakeInsertResult()

    async def insert_many(self, docs):
        docs = [dict(doc) for doc in docs]
        self.docs.extend(docs)
        return _FakeInsertManyResult(len(docs))

    async def update_one(self, query, update, upsert=False):
        patch = dict(update.get("$set", update))
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                doc.update(patch)
                return _FakeInsertResult()
        if upsert:
            self.docs.append({**dict(query), **patch})
            return _FakeInsertResult()
        return _FakeInsertResult()

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                if projection:
                    return {
                        key: doc.get(key)
                        for key, enabled in projection.items()
                        if enabled and key in doc
                    }
                return dict(doc)
        return None


class _FakeDB:
    def __init__(self) -> None:
        self.collections = {}

    def __getitem__(self, name):
        self.collections.setdefault(name, _FakeCollection())
        return self.collections[name]


def test_info_collection_streaming_helper_runs_stage_graph():
    async def _run():
        from core.stream import Stage

        class _EmitStage(Stage):
            name = "emit"

            async def handle(self, item, ctx) -> None:
                await ctx.emit("sink", {
                    "payload": item.payload,
                    "idx": item.meta["idx"],
                    "total": item.meta["total"],
                })

        class _SinkStage(Stage):
            name = "sink"

            async def handle(self, item, ctx) -> None:
                ctx.state.setdefault("seen", []).append(item.payload)

        state = {}
        pipe = await run_stream_pipeline(
            stages=[
                stream_stage(_EmitStage(), downstream=["sink"]),
                stream_stage(_SinkStage()),
            ],
            seeds=make_stream_items(["a", "b"], indexed=True),
            entry="emit",
            state=state,
            on_pipeline_ready=lambda ready_pipe: ready_pipe.state.update({"ready": True}),
        )

        assert pipe.state is state
        assert state["ready"] is True
        assert state["seen"] == [
            {"payload": "a", "idx": 0, "total": 2},
            {"payload": "b", "idx": 1, "total": 2},
        ]
        assert pipe.metrics_summary()["emit"]["emitted"]["sink"] == 2

    asyncio.run(_run())


def test_company_xhs_search_stage_uses_search_tool_contract():
    async def _run():
        tool = _FakeSearchTool()
        ctx = _FakeContext({"xhs_search_tool": tool})
        stage = XhsSearchStage(
            concurrency=1,
            project_id="project-1",
            task_id="task-1",
            per_keyword=5,
            db=None,
            pipeline_owner=None,
        )

        item = type("Item", (), {"payload": "目标公司 招聘", "meta": {"idx": 2, "total": 4}})()
        await stage.handle(item, ctx)

        assert len(tool.requests) == 1
        req = tool.requests[0]
        assert req.source == "xhs"
        assert req.query == "目标公司 招聘"
        assert req.project_id == "project-1"
        assert req.task_id == "task-1_xhs_2"
        assert req.limit == 5
        assert req.options["sort_type"] == "time_descending"

        assert ctx.state["all_notes_count"] == 1
        assert len(ctx.emitted) == 1
        target_stage, note = ctx.emitted[0]
        assert target_stage == "tagging"
        assert note["_keyword"] == "目标公司 招聘"
        assert note["_sub_task_id"] == "task-1_xhs_2"

    asyncio.run(_run())


def test_url_scan_stage_uses_scan_tool_and_preserves_streaming_emit():
    async def _run():
        seen_results = []

        async def on_result(result):
            seen_results.append(result)

        tool = _FakeScanTool()
        stage = _UrlScanStage(
            concurrency=1,
            project_id="project-1",
            task_id="task-1",
            on_result=on_result,
            emit_to="copywriting",
        )
        ctx = _FakeContext({"url_scan_tool": tool, "scan_results": []})
        item = type("Item", (), {
            "payload": {"url": "https://example.com", "title": "Example"},
            "meta": {},
            "item_id": "item-1",
            "attempt": 1,
        })()

        await stage.handle(item, ctx)

        assert len(tool.requests) == 1
        req = tool.requests[0]
        assert req.source == "web_tagging"
        assert req.target == "https://example.com"
        assert req.project_id == "project-1"
        assert req.task_id == "task-1"
        assert req.target_info["title"] == "Example"
        assert req.options["pipeline_id"] == "pipe-1"
        assert req.options["item_id"] == "item-1"
        assert req.options["attempt"] == 1

        assert len(ctx.state["scan_results"]) == 1
        assert ctx.state["scan_results"][0]["data"]["findings"][0]["label"] == "HR 邮箱"
        assert seen_results == ctx.state["scan_results"]
        assert len(ctx.emitted) == 1
        assert ctx.emitted[0][0] == "copywriting"
        assert ctx.emitted[0][1] == ctx.state["scan_results"][0]

    asyncio.run(_run())


def test_url_scan_urls_runs_through_shared_stream_helper(monkeypatch):
    async def _run():
        tool = _FakeScanTool()

        class _Toolset:
            def state(self):
                return {
                    "url_scan_tool": tool,
                    "copywriting_tool": None,
                    "url_probe_tool": None,
                }

        monkeypatch.setattr(
            InfoCollectionToolFactory,
            "create_url_toolset",
            lambda self, response_parser=None: _Toolset(),
        )

        seen_results = []

        async def on_result(result):
            seen_results.append(result)

        pipeline = UrlScanPipeline(_FakeDB(), object())
        results = await pipeline.scan_urls(
            project_id="project-1",
            task_id="task-1",
            alive_urls=[
                {"url": "https://a.example", "title": "A"},
                {"url": "https://b.example", "title": "B"},
            ],
            num_workers=2,
            on_result=on_result,
        )

        assert len(tool.requests) == 2
        assert sorted(req.target for req in tool.requests) == [
            "https://a.example",
            "https://b.example",
        ]
        assert sorted(result["url"] for result in results) == [
            "https://a.example",
            "https://b.example",
        ]
        assert sorted(result["url"] for result in seen_results) == [
            "https://a.example",
            "https://b.example",
        ]

    asyncio.run(_run())


def test_url_scan_pipeline_streams_findings_to_copywriting(monkeypatch):
    async def _run():
        scan_requests = []
        copywriting_requests = []

        class _ScanTool:
            name = "url_web_scan"

            async def scan(self, request):
                scan_requests.append(request)
                return ScanResult(
                    source=request.source,
                    target=request.target,
                    success=True,
                    data={
                        "intro": {
                            "domain": "example.com",
                            "site_name": "Example",
                            "entity_name": "目标公司",
                            "summary": "招聘站点",
                        },
                        "findings": [{
                            "finding_id": "finding-1",
                            "type": "hr_contact",
                            "channel": "email",
                            "role": "hr",
                            "label": "HR 邮箱",
                            "value": "hr@example.com",
                            "context": "页面展示 HR 邮箱",
                            "evidence": "hr@example.com",
                            "attention_score": 82,
                            "attention_reason": "招聘联系方式",
                        }],
                    },
                )

        class _CopywritingTool:
            name = "agent_copywriting"

            async def generate(self, request):
                copywriting_requests.append(request)
                return CopywritingResult(
                    source=request.source,
                    project_id=request.project_id,
                    task_id=request.task_id,
                    target_id=request.target_id,
                    copywritings=[{
                        "finding_id": request.target_id,
                        "url": request.options["url"],
                        "status": "completed",
                    }],
                )

        class _Toolset:
            def state(self):
                return {
                    "url_scan_tool": _ScanTool(),
                    "copywriting_tool": _CopywritingTool(),
                    "url_probe_tool": None,
                }

        probe_calls = []

        async def fake_probe_urls(urls, concurrency=20, timeout=10.0):
            probe_calls.append({"urls": urls, "concurrency": concurrency, "timeout": timeout})
            return [{"url": urls[0], "title": "Example", "status_code": 200}]

        monkeypatch.setattr(
            InfoCollectionToolFactory,
            "create_url_toolset",
            lambda self, response_parser=None: _Toolset(),
        )
        monkeypatch.setattr(UrlScanPipeline, "probe_urls", staticmethod(fake_probe_urls))

        db = _FakeDB()
        pipeline = UrlScanPipeline(db, object())
        result = await pipeline.run_pipeline(
            task_id="task-1",
            project_id="project-1",
            url_content="example.com",
            target_id="target-1",
            min_attention_score=40,
            known_alive_urls=["https://example.com"],
            source="bidding",
            source_context_by_url={
                "https://example.com": "上游公告正文",
            },
        )

        assert result["status"] == "completed"
        assert result["alive_urls"] == 1
        assert result["probed_urls"] == 0
        assert result["reused_alive_urls"] == 1
        assert result["scanned_urls"] == 1
        assert result["total_findings"] == 1
        assert result["total_copywritings"] == 1
        assert len(scan_requests) == 1
        assert scan_requests[0].source == "bidding"
        assert scan_requests[0].target_info["target_id"] == "target-1"
        assert scan_requests[0].target_info["source_context"] == "上游公告正文"
        assert len(copywriting_requests) == 1
        assert copywriting_requests[0].source == "bidding"
        assert copywriting_requests[0].target_id == "finding-1"
        assert db["findings"].docs[0]["source"] == "bidding"
        assert db["findings"].docs[0]["target_id"] == "target-1"
        assert db["url_scan_results"].docs[0]["url"] == "https://example.com"
        assert db["copywritings"].docs[0]["finding_id"] == "finding-1"
        assert probe_calls == []

    asyncio.run(_run())


def test_company_xhs_tagging_stage_uses_tagging_tool_contract(monkeypatch):
    async def _run():
        from api.dao import xhs as xhs_dao

        stored = {}

        async def fake_update_note_tagging(db, note_id, tagging):
            stored["tagging"] = {"note_id": note_id, "tagging": tagging}

        monkeypatch.setattr(xhs_dao, "update_note_tagging", fake_update_note_tagging)

        tool = _FakeTaggingTool("note", score=85)
        stage = XhsTaggingStage(
            concurrency=1,
            attention_threshold=60,
            db=None,
            pipeline_owner=None,
        )
        ctx = _FakeContext({
            "xhs_note_tagging_tool": tool,
            "tagging_count": 0,
            "all_suspicious_count": 0,
        })
        item = type(
            "Item",
            (),
            {
                "payload": {
                    "note_id": "note-1",
                    "project_id": "project-1",
                    "task_id": "task-1_xhs_0",
                    "_keyword": "目标公司 招聘",
                    "_sub_task_id": "task-1_xhs_0",
                },
                "meta": {},
            },
        )()

        await stage.handle(item, ctx)

        assert len(tool.requests) == 1
        req = tool.requests[0]
        assert req.kind == "note"
        assert req.item_id == "note-1"
        assert req.project_id == "project-1"
        assert req.task_id == "task-1_xhs_0"
        assert req.context["keyword"] == "目标公司 招聘"
        assert stored["tagging"]["tagging"]["attention_score"] == 85
        assert ctx.state["all_suspicious_count"] == 1
        assert len(ctx.emitted) == 1
        assert ctx.emitted[0][0] == "detail"

    asyncio.run(_run())


def test_xhs_note_tagging_persist_stage_uses_tool_without_downstream(monkeypatch):
    async def _run():
        from api.dao import xhs as xhs_dao

        stored = []

        async def fake_update_note_tagging(db, note_id, tagging):
            stored.append({"note_id": note_id, "tagging": tagging})

        monkeypatch.setattr(xhs_dao, "update_note_tagging", fake_update_note_tagging)

        tool = _FakeTaggingTool("note", score=75)
        stage = XhsNoteTaggingPersistStage(
            concurrency=1,
            db=None,
            keyword="目标公司",
        )
        ctx = _FakeContext({
            "xhs_note_tagging_tool": tool,
            "tagging_count": 0,
        })
        item = type(
            "Item",
            (),
            {
                "payload": {
                    "note_id": "note-1",
                    "project_id": "project-1",
                    "task_id": "task-1",
                },
                "meta": {},
            },
        )()

        await stage.handle(item, ctx)

        assert len(tool.requests) == 1
        req = tool.requests[0]
        assert req.kind == "note"
        assert req.item_id == "note-1"
        assert req.context["keyword"] == "目标公司"
        assert stored == [{
            "note_id": "note-1",
            "tagging": {
                "attention_score": 75,
                "summary": "detail-summary",
                "findings": [],
            },
        }]
        assert ctx.emitted == []
        assert ctx.state["tagging_count"] == 1

    asyncio.run(_run())


def test_company_xhs_detail_stage_uses_detail_tool_contract(monkeypatch):
    async def _run():
        from api.dao import xhs as xhs_dao

        stored = {}

        async def fake_create_note_detail(db, **kwargs):
            stored["detail"] = kwargs

        async def fake_update_note_detail_tagging(db, note_id, tagging):
            stored["tagging"] = {"note_id": note_id, "tagging": tagging}

        async def fake_insert_finding(db, finding):
            stored.setdefault("findings", []).append(finding)

        monkeypatch.setattr(xhs_dao, "create_note_detail", fake_create_note_detail)
        monkeypatch.setattr(xhs_dao, "update_note_detail_tagging", fake_update_note_detail_tagging)
        from api.dao import findings as findings_dao

        monkeypatch.setattr(findings_dao, "insert_finding", fake_insert_finding)

        detail_tool = _FakeDetailTool()
        tagging_tool = _FakeTaggingTool(
            "detail",
            score=80,
            findings=[{
                "type": "personal_info",
                "value": "疑似员工",
                "attention_reason": "命中目标公司",
                "evidence": "笔记详情证据",
            }],
        )
        stage = XhsDetailStage(
            concurrency=1,
            project_id="project-1",
            db=None,
            pipeline_owner=None,
        )
        ctx = _FakeContext({
            "xhs_detail_tool": detail_tool,
            "xhs_detail_tagging_tool": tagging_tool,
        })
        item = type(
            "Item",
            (),
            {
                "payload": {
                    "note_id": "note-1",
                    "xsec_token": "token-1",
                    "xsec_source": "pc_feed",
                    "_sub_task_id": "task-1_xhs_0",
                    "user": {
                        "user_id": "user-1",
                        "nickname": "测试用户",
                    },
                },
                "meta": {},
            },
        )()

        await stage.handle(item, ctx)

        assert len(detail_tool.requests) == 1
        req = detail_tool.requests[0]
        assert req.source == "xhs"
        assert req.item_id == "note-1"
        assert req.project_id == "project-1"
        assert req.task_id == "task-1_xhs_0"
        assert req.xsec_token == "token-1"
        assert req.xsec_source == "pc_feed"
        assert len(tagging_tool.requests) == 1
        tag_req = tagging_tool.requests[0]
        assert tag_req.kind == "detail"
        assert tag_req.item_id == "note-1"
        assert tag_req.context["content"] == "detail-content"
        assert tag_req.context["comments_summary"] == "comment-summary"

        assert stored["detail"]["content"] == "detail-content"
        assert stored["detail"]["comments_summary"] == "comment-summary"
        assert stored["detail"]["images_urls"] == ["https://img.example/a.jpg"]
        assert stored["detail"]["xsec_source"] == "pc_feed"
        assert stored["tagging"]["note_id"] == "note-1"
        assert stored["tagging"]["tagging"]["attention_score"] == 80
        assert stored["findings"][0]["source"] == "xhs"
        assert stored["findings"][0]["channel"] == "xhs_note_detail"
        assert stored["findings"][0]["xhs_user_id"] == "user-1"
        assert stored["findings"][0]["xhs_note_ids"] == ["note-1"]
        assert stored["findings"][0]["attention_score"] == 80
        assert ctx.state["detail_count"] == 1
        assert ctx.state["detail_findings_count"] == 1
        assert ctx.state["comments_count"] == 0
        assert ctx.state["images_count"] == 1

    asyncio.run(_run())


def test_xhs_prefetched_detail_tagging_stage_uses_tagging_tool_contract(monkeypatch):
    async def _run():
        from api.dao import findings as findings_dao
        from api.dao import xhs as xhs_dao

        stored = {
            "details": [],
            "tagging": [],
            "findings": [],
        }

        async def fake_create_note_detail(db, **kwargs):
            stored["details"].append(dict(kwargs))

        async def fake_update_note_detail_tagging(db, note_id, tagging):
            stored["tagging"].append({"note_id": note_id, "tagging": dict(tagging)})

        async def fake_insert_finding(db, finding):
            stored["findings"].append(dict(finding))

        monkeypatch.setattr(xhs_dao, "create_note_detail", fake_create_note_detail)
        monkeypatch.setattr(xhs_dao, "update_note_detail_tagging", fake_update_note_detail_tagging)
        monkeypatch.setattr(findings_dao, "insert_finding", fake_insert_finding)

        tagging_tool = _FakeTaggingTool(
            "detail",
            score=88,
            findings=[{
                "type": "personal_info",
                "value": "疑似员工",
                "attention_reason": "详情命中目标公司",
                "evidence": "评论区证据",
            }],
        )
        stage = XhsPrefetchedDetailTaggingStage(
            concurrency=2,
            project_id="project-1",
            db=None,
        )
        ctx = _FakeContext({
            "xhs_detail_tagging_tool": tagging_tool,
            "detail_count": 0,
            "detail_findings_count": 0,
        })
        item = type(
            "Item",
            (),
            {
                "payload": {
                    "note": {
                        "note_id": "note-1",
                        "xsec_token": "token-1",
                        "xsec_source": "pc_feed",
                        "_sub_task_id": "task-1_xhs_0",
                        "user": {
                            "user_id": "user-1",
                            "nickname": "测试用户",
                        },
                    },
                    "content": "预取详情正文",
                    "comments_summary": "预取评论摘要",
                    "comments_data": [{"content": "评论"}],
                    "images_urls": ["https://img.example/a.jpg"],
                },
                "meta": {},
            },
        )()

        await stage.handle(item, ctx)

        assert stored["details"] == [{
            "note_id": "note-1",
            "project_id": "project-1",
            "content": "预取详情正文",
            "comments_summary": "预取评论摘要",
            "comments_data": [{"content": "评论"}],
            "images_urls": ["https://img.example/a.jpg"],
            "xsec_token": "token-1",
            "xsec_source": "pc_feed",
        }]
        assert len(tagging_tool.requests) == 1
        tag_req = tagging_tool.requests[0]
        assert tag_req.kind == "detail"
        assert tag_req.item_id == "note-1"
        assert tag_req.project_id == "project-1"
        assert tag_req.task_id == "task-1_xhs_0"
        assert tag_req.context["content"] == "预取详情正文"
        assert tag_req.context["comments_summary"] == "预取评论摘要"
        assert stored["tagging"][0]["note_id"] == "note-1"
        assert stored["tagging"][0]["tagging"]["attention_score"] == 88
        assert stored["findings"][0]["project_id"] == "project-1"
        assert stored["findings"][0]["task_id"] == "task-1_xhs_0"
        assert stored["findings"][0]["xhs_user_id"] == "user-1"
        assert stored["findings"][0]["xhs_note_ids"] == ["note-1"]
        assert stored["findings"][0]["attention_score"] == 88
        assert ctx.state["detail_count"] == 1
        assert ctx.state["detail_findings_count"] == 1

    asyncio.run(_run())


def test_xhs_profile_tool_delegates_to_pipeline_runtime():
    async def _run():
        calls = []

        class _Owner:
            async def _stage_profile_generation(
                self,
                task_id,
                project_id,
                keyword,
                screenshot_concurrency=2,
                profile_concurrency=3,
            ):
                calls.append({
                    "task_id": task_id,
                    "project_id": project_id,
                    "keyword": keyword,
                    "screenshot_concurrency": screenshot_concurrency,
                    "profile_concurrency": profile_concurrency,
                })
                return [{"user_id": "user-1"}]

        tool = XhsProfileTool(_Owner())
        result = await tool.generate_profile(
            ProfileRequest(
                source="xhs",
                project_id="project-1",
                task_id="task-1",
                keyword="目标公司",
                options={"screenshot_concurrency": 4, "profile_concurrency": 5},
            )
        )

        assert result.source == "xhs"
        assert result.project_id == "project-1"
        assert result.task_id == "task-1"
        assert result.count == 1
        assert calls == [{
            "task_id": "task-1",
            "project_id": "project-1",
            "keyword": "目标公司",
            "screenshot_concurrency": 4,
            "profile_concurrency": 5,
        }]

    asyncio.run(_run())


def test_agent_copywriting_tool_normalizes_agent_output():
    async def _run():
        class _Agent:
            def __init__(self) -> None:
                self.calls = []

            async def __call__(self, payload):
                self.calls.append(payload)
                return {"messages": []}

        agent = _Agent()
        tool = AgentCopywritingTool(
            agent=agent,
            response_parser=lambda _raw: [{"finding_id": "finding-1", "url": "https://example.com"}],
        )
        result = await tool.generate(
            CopywritingRequest(
                source="web_tagging",
                project_id="project-1",
                task_id="task-1",
                target_id="finding-1",
                context="生成话术上下文",
            )
        )

        assert result.ok
        assert result.count == 1
        assert result.copywritings[0]["status"] == "completed"
        assert result.copywritings[0]["finding_id"] == "finding-1"
        assert agent.calls[0]["messages"][0].content == "生成话术上下文"

    asyncio.run(_run())


def test_url_probe_tool_normalizes_probe_results():
    async def _run():
        calls = []

        class _ProbeItem:
            url = "https://a.example"
            status_code = 200
            title = "A"
            response_time = 0.12

        async def probe_func(**kwargs):
            calls.append(kwargs)
            return [_ProbeItem(), {"url": "https://b.example", "status_code": 204}]

        result = await UrlProbeTool(probe_func=probe_func).probe(
            ProbeRequest(
                source="url_scan",
                urls=["https://a.example", "https://b.example"],
                project_id="project-1",
                task_id="task-1",
                concurrency=5,
                timeout=3.5,
                only_alive=True,
            )
        )

        assert result.count == 2
        assert calls == [{
            "urls": ["https://a.example", "https://b.example"],
            "concurrency": 5,
            "timeout": 3.5,
            "only_alive": True,
        }]
        assert result.items[0] == {
            "url": "https://a.example",
            "status_code": 200,
            "title": "A",
            "response_time": 0.12,
        }
        assert result.items[1]["url"] == "https://b.example"
        assert result.meta["project_id"] == "project-1"

    asyncio.run(_run())


def test_hunter_search_probe_tool_uses_search_contract():
    async def _run():
        calls = []

        async def search_func(**kwargs):
            calls.append(kwargs)
            return [{"url": "https://target.example", "status_code": 200}]

        result = await HunterSearchProbeTool(search_func=search_func).search(
            SearchRequest(
                source="hunter",
                query="目标公司",
                project_id="project-1",
                task_id="task-1",
                limit=30,
                options={
                    "search_type": "icp",
                    "probe_concurrency": 8,
                    "probe_timeout": 4.0,
                },
            )
        )

        assert result.count == 1
        assert calls == [{
            "query": "目标公司",
            "search_type": "icp",
            "size": 30,
            "probe_concurrency": 8,
            "probe_timeout": 4.0,
        }]
        assert result.items[0]["url"] == "https://target.example"
        assert result.meta["project_id"] == "project-1"
        assert result.meta["probe_concurrency"] == 8

    asyncio.run(_run())


def test_url_scan_probe_urls_uses_probe_tool(monkeypatch):
    async def _run():
        from api.services.info_collection import url_tools as url_tools_module
        from api.services.url_scan_pipeline import UrlScanPipeline

        calls = []

        async def fake_probe(self, request):
            calls.append(request)
            return ProbeResult(
                source=request.source,
                items=[{"url": "https://a.example", "status_code": 200}],
            )

        monkeypatch.setattr(url_tools_module.UrlProbeTool, "probe", fake_probe)

        result = await UrlScanPipeline.probe_urls(
            ["https://a.example"],
            concurrency=7,
            timeout=2.5,
        )

        assert result == [{"url": "https://a.example", "status_code": 200}]
        req = calls[0]
        assert req.source == "url_scan"
        assert req.urls == ["https://a.example"]
        assert req.concurrency == 7
        assert req.timeout == 2.5
        assert req.only_alive is True

    asyncio.run(_run())


def test_profile_copywriting_stage_uses_copywriting_tool_contract():
    async def _run():
        db = _FakeDB()
        db["findings"].docs.append({
            "project_id": "project-1",
            "xhs_user_id": "user-1",
            "finding_id": "finding-1",
        })

        class _Owner:
            def _build_profile_copywriting_context(self, profile, company_name, router_output):
                assert profile["user_id"] == "user-1"
                assert company_name == "目标公司"
                assert router_output.success is False
                return "画像话术上下文"

        router_output = type("RouterOutput", (), {"success": False})()
        tool = _FakeCopywritingTool()
        stage = _ProfileCopywritingStage(
            concurrency=1,
            project_id="project-1",
            task_id="task-1",
            company_name="目标公司",
            router_output=router_output,
            db=db,
            pipeline_owner=_Owner(),
        )
        ctx = _FakeContext({
            "profile_copywriting_tool": tool,
            "profile_copywriting_count": 0,
            "_profile_copywriting_schema_json": "{}",
        })
        item = type("Item", (), {
            "payload": {
                "user_id": "user-1",
                "nickname": "测试用户",
                "attention_score": 88,
            },
            "meta": {},
        })()

        await stage.handle(item, ctx)

        assert len(tool.requests) == 1
        req = tool.requests[0]
        assert req.source == "xhs_profile"
        assert req.project_id == "project-1"
        assert req.task_id == "task-1"
        assert req.target_id == "user-1"
        assert "画像话术上下文" in req.context
        assert req.options["url"].endswith("/user/profile/user-1")

        profile_docs = db[PROFILE_COPYWRITINGS_COLLECTION].docs
        unified_docs = db[COPYWRITINGS_COLLECTION].docs
        assert len(profile_docs) == 1
        assert len(unified_docs) == 1
        assert profile_docs[0]["source"] == "xhs_profile"
        assert profile_docs[0]["user_id"] == "user-1"
        assert unified_docs[0]["finding_id"] == "finding-1"
        assert ctx.state["profile_copywriting_count"] == 1

    asyncio.run(_run())


def test_url_copywriting_stage_uses_copywriting_tool_contract():
    async def _run():
        db = _FakeDB()
        tool = _FakeCopywritingTool()

        class _Owner:
            def build_copywriting_request(
                self,
                finding,
                site_context,
                siblings,
                *,
                project_id="",
                task_id="",
            ):
                assert finding["finding_id"] == "finding-1"
                assert site_context["domain"] == "example.com"
                assert siblings == []
                return CopywritingRequest(
                    source="web_tagging",
                    project_id=project_id,
                    task_id=task_id,
                    target_id=finding["finding_id"],
                    target=finding,
                    context="URL 话术上下文",
                    options={"url": finding["url"]},
                )

        stage = _UrlCopywritingStage(
            concurrency=1,
            project_id="project-1",
            task_id="task-1",
            pipeline_owner=_Owner(),
            score_threshold=60,
        )
        ctx = _FakeContext({
            "db": db,
            "copywriting_tool": tool,
            "copywriting_count": 0,
        })
        finding = {
            "finding_id": "finding-1",
            "url": "https://example.com",
            "attention_score": 80,
            "label": "联系方式",
        }
        site_context = {"domain": "example.com"}
        item = type("Item", (), {"payload": (finding, site_context, []), "meta": {}})()

        await stage.handle(item, ctx)

        assert len(tool.requests) == 1
        req = tool.requests[0]
        assert req.source == "web_tagging"
        assert req.project_id == "project-1"
        assert req.task_id == "task-1"
        assert req.target_id == "finding-1"
        assert req.context == "URL 话术上下文"

        docs = db[COPYWRITINGS_COLLECTION].docs
        assert len(docs) == 1
        assert docs[0]["source"] == "web_tagging"
        assert docs[0]["task_id"] == "task-1"
        assert docs[0]["project_id"] == "project-1"
        assert ctx.state["copywriting_count"] == 1

    asyncio.run(_run())


def test_info_collection_factory_creates_url_toolset():
    app_config = type("Config", (), {"mcp_servers": {}})()
    factory = InfoCollectionToolFactory(db=_FakeDB(), app_config=app_config)

    toolset = factory.create_url_toolset(response_parser=lambda raw: raw)
    state = toolset.state()

    assert state["url_scan_tool"].name == "url_web_scan"
    assert state["copywriting_tool"].name == "agent_copywriting"
    assert state["url_probe_tool"].name == "url_probe"
    assert toolset.probe_tool.name == "url_probe"
    assert factory.create_copywriting_tool(response_parser=lambda raw: raw).name == "agent_copywriting"
    assert factory.create_hunter_search_tool().name == "hunter_search_probe"


def test_info_collection_factory_creates_xhs_toolset_without_active_cookie():
    async def _run():
        class _Owner:
            async def _get_note_tagging_agent(self):
                return object()

            async def _get_detail_tagging_agent(self):
                return object()

        app_config = type("Config", (), {"mcp_servers": {}})()
        toolset = await InfoCollectionToolFactory(db=_FakeDB(), app_config=app_config).create_xhs_toolset(
            _Owner()
        )
        state = toolset.state()

        assert state["v2_client"] is None
        assert state["xhs_search_tool"].name == "xhs_search"
        assert state["xhs_detail_tool"].name == "xhs_detail"
        assert state["xhs_note_tagging_tool"].name == "xhs_note_tagging"
        assert state["xhs_detail_tagging_tool"].name == "xhs_detail_tagging"
        assert state["xhs_profile_tool"].name == "xhs_profile"
        assert toolset.profile_tool.name == "xhs_profile"
        await toolset.close()

    asyncio.run(_run())


def test_xhs_search_tool_rotates_accounts_and_persists_v2_results(monkeypatch):
    async def _run():
        from api.dao import xhs as xhs_dao
        from api.services.xhs_runtime import XhsAccountLease, XhsProxyLease

        calls = {
            "accounts": [],
            "clients": [],
            "records": [],
            "stored": [],
        }
        accounts = [
            XhsAccountLease(account_name="account-a", cookie_string="cookie-a", source="pool:test"),
            XhsAccountLease(account_name="account-b", cookie_string="cookie-b", source="pool:test"),
        ]

        async def runtime_config_loader():
            return {
                "account_pool": {
                    "search_pages_per_account": 1,
                    "search_retries_per_page": 1,
                    "search_max_pages_per_keyword": 2,
                    "request_interval_min_seconds": 0,
                    "request_interval_max_seconds": 0,
                },
                "proxy_pool": {"request_timeout": 12.5},
            }

        async def account_selector(db, purpose, config, exclude_accounts):
            calls["accounts"].append({
                "purpose": purpose,
                "exclude_accounts": list(exclude_accounts),
            })
            return accounts.pop(0)

        async def proxy_selector(config):
            return XhsProxyLease(proxy_url="http://127.0.0.1:8080", source="static")

        async def result_recorder(db, account_name, **kwargs):
            calls["records"].append({"account_name": account_name, **kwargs})

        async def fake_create_notes_batch(db, notes):
            calls["stored"].extend([dict(note) for note in notes])

        async def no_sleep(_seconds):
            return None

        class _Client:
            def __init__(self, cookie_string, proxy_url=None, request_timeout=30.0) -> None:
                self.cookie_string = cookie_string
                calls["clients"].append({
                    "cookie_string": cookie_string,
                    "proxy_url": proxy_url,
                    "request_timeout": request_timeout,
                })

            async def pong(self):
                return True

            async def search_notes(self, keyword, page, page_size, sort):
                return {
                    "items": [{
                        "id": f"note-{page}",
                        "xsec_token": f"token-{page}",
                        "xsec_source": "pc_feed",
                        "note_card": {
                            "display_title": f"{keyword}-{page}",
                            "desc": "desc",
                            "type": "normal",
                            "interact_info": {"liked_count": "10"},
                            "user": {"user_id": f"user-{page}", "nickname": f"用户{page}"},
                            "cover": {"url_default": f"https://img.example/{page}.jpg"},
                            "corner_tag_info": [{"type": "publish_time", "text": "1天前"}],
                        },
                    }],
                    "has_more": page < 2,
                }

            async def close(self):
                return None

        monkeypatch.setattr(xhs_dao, "create_notes_batch", fake_create_notes_batch)

        result = await XhsSearchTool(
            db=_FakeDB(),
            runtime_config_loader=runtime_config_loader,
            account_selector=account_selector,
            proxy_selector=proxy_selector,
            result_recorder=result_recorder,
            client_factory=_Client,
            sleep_func=no_sleep,
        ).search(
            SearchRequest(
                source="xhs",
                query="目标公司",
                project_id="project-1",
                task_id="task-1",
                limit=40,
                options={"sort_type": "general"},
            )
        )

        assert result.count == 2
        assert [call["purpose"] for call in calls["accounts"]] == ["search", "search"]
        assert [client["cookie_string"] for client in calls["clients"]] == ["cookie-a", "cookie-b"]
        assert all(client["proxy_url"] == "http://127.0.0.1:8080" for client in calls["clients"])
        assert all(client["request_timeout"] == 12.5 for client in calls["clients"])
        assert [note["note_id"] for note in calls["stored"]] == ["note-1", "note-2"]
        assert result.items[0]["project_id"] == "project-1"
        assert result.items[0]["task_id"] == "task-1"
        assert result.items[0]["publish_time_text"] == "1天前"
        assert {record["account_name"] for record in calls["records"]} == {"account-a", "account-b"}

    asyncio.run(_run())


def test_xhs_stage_search_wrapper_uses_search_tool(monkeypatch):
    async def _run():
        from api.services import xhs_pipeline as xhs_pipeline_module
        from api.services.info_collection import xhs_tools as xhs_tools_module

        calls = {"init": [], "requests": []}

        class _SearchTool:
            name = "xhs_search"

            def __init__(self, **kwargs) -> None:
                calls["init"].append(kwargs)

            async def search(self, request):
                calls["requests"].append(request)
                return SearchResult(
                    source="xhs",
                    query=request.query,
                    items=[{"note_id": "note-1"}],
                )

        monkeypatch.setattr(xhs_tools_module, "XhsSearchTool", _SearchTool)

        pipeline = xhs_pipeline_module.XhsPipeline(_FakeDB(), object())
        result = await pipeline._stage_search(
            task_id="task-1",
            project_id="project-1",
            keyword="目标公司",
            max_notes=10,
            sort_type="general",
        )

        assert result == [{"note_id": "note-1"}]
        assert calls["init"][0]["db"] is pipeline.db
        assert calls["init"][0]["crawler_factory"] == pipeline._get_crawler
        req = calls["requests"][0]
        assert req.source == "xhs"
        assert req.query == "目标公司"
        assert req.project_id == "project-1"
        assert req.task_id == "task-1"
        assert req.limit == 10
        assert req.options["sort_type"] == "general"

    asyncio.run(_run())


def test_xhs_stage_note_tagging_wrapper_runs_streaming_tool(monkeypatch):
    async def _run():
        from api.dao import xhs as xhs_dao
        from api.services.xhs_pipeline import XhsPipeline

        stored = []
        active = 0
        max_active = 0
        lock = asyncio.Lock()

        async def fake_update_note_tagging(db, note_id, tagging):
            stored.append({"note_id": note_id, "tagging": tagging})

        class _Tool:
            name = "xhs_note_tagging"

            async def tag(self, request):
                nonlocal active, max_active
                async with lock:
                    active += 1
                    max_active = max(max_active, active)
                await asyncio.sleep(0.02)
                async with lock:
                    active -= 1
                return TagResult(
                    source="xhs",
                    kind="note",
                    item_id=request.item_id,
                    tagging={"attention_score": 80, "keyword": request.context["keyword"]},
                )

        async def fake_create_xhs_note_tagging_tool(self, pipeline_owner):
            return _Tool()

        async def fail_old_agent(self):
            raise AssertionError("旧串行 note tagging 不应直接初始化 agent")

        monkeypatch.setattr(xhs_dao, "update_note_tagging", fake_update_note_tagging)
        monkeypatch.setattr(
            InfoCollectionToolFactory,
            "create_xhs_note_tagging_tool",
            fake_create_xhs_note_tagging_tool,
        )
        monkeypatch.setattr(XhsPipeline, "_get_note_tagging_agent", fail_old_agent)

        pipeline = XhsPipeline(_FakeDB(), object())
        await pipeline._stage_note_tagging(
            [
                {"note_id": "note-1", "project_id": "project-1"},
                {"note_id": "note-2", "project_id": "project-1"},
                {"note_id": "note-3", "project_id": "project-1"},
            ],
            keyword="目标公司",
        )

        assert sorted(item["note_id"] for item in stored) == ["note-1", "note-2", "note-3"]
        assert {item["tagging"]["keyword"] for item in stored} == {"目标公司"}
        assert max_active > 1

    asyncio.run(_run())


def test_xhs_stage_detail_tagging_wrapper_runs_prefetched_stream(monkeypatch):
    async def _run():
        from api.dao import findings as findings_dao
        from api.dao import xhs as xhs_dao
        from api.services import xhs_pipeline as xhs_pipeline_module
        from api.services import xhs_runtime
        from api.services.info_collection.factory import InfoCollectionToolFactory
        from api.services.xhs_pipeline import XhsPipeline

        stored = {
            "details": [],
            "detail_tagging": [],
            "findings": [],
            "records": [],
        }
        active = 0
        max_active = 0
        lock = asyncio.Lock()

        class _Account:
            account_name = "account-1"
            cookie_string = "cookie-1"
            source = "pool:test"

        class _Proxy:
            proxy_url = "http://127.0.0.1:8080"

            def to_dict(self):
                return {"proxy_url": self.proxy_url}

        class _Client:
            def __init__(self, cookie_string, proxy_url=None, request_timeout=30.0) -> None:
                self.cookie_string = cookie_string
                self.proxy_url = proxy_url
                self.request_timeout = request_timeout
                self.closed = False

            async def get_note_by_id(self, note_id, xsec_token="", xsec_source=""):
                return {
                    "desc": f"detail:{note_id}",
                    "image_list": [{"url_default": f"https://img.example/{note_id}.jpg"}],
                }

            async def close(self):
                self.closed = True

        class _DetailTaggingTool:
            name = "xhs_detail_tagging"

            def __init__(self) -> None:
                self.requests = []

            async def tag(self, request):
                nonlocal active, max_active
                self.requests.append(request)
                async with lock:
                    active += 1
                    max_active = max(max_active, active)
                await asyncio.sleep(0.02)
                async with lock:
                    active -= 1
                return TagResult(
                    source="xhs",
                    kind="detail",
                    item_id=request.item_id,
                    tagging={
                        "attention_score": 82,
                        "summary": request.context["content"],
                        "findings": [{
                            "type": "personal_info",
                            "value": "疑似员工",
                            "attention_reason": "命中",
                            "evidence": request.context["comments_summary"],
                        }],
                    },
                )

        tool = _DetailTaggingTool()

        async def fake_get_runtime_config():
            return {"proxy_pool": {"request_timeout": 10.0}}

        async def fake_select_account(db, purpose, config):
            assert purpose == "detail"
            return _Account()

        async def fake_select_proxy(config):
            return _Proxy()

        async def fake_record_account_result(db, account_name, **kwargs):
            stored["records"].append({"account_name": account_name, **kwargs})

        async def fake_create_note_detail(db, **kwargs):
            stored["details"].append(dict(kwargs))

        async def fake_update_note_detail_tagging(db, note_id, tagging):
            stored["detail_tagging"].append({"note_id": note_id, "tagging": dict(tagging)})

        async def fake_insert_finding(db, finding):
            stored["findings"].append(dict(finding))

        async def fake_create_xhs_detail_tagging_tool(self, pipeline_owner):
            return tool

        async def fake_get_crawler(self):
            return object()

        async def fail_old_detail_agent(self):
            raise AssertionError("旧串行详情打标不应直接初始化 agent")

        monkeypatch.setattr(xhs_runtime, "get_xhs_runtime_config", fake_get_runtime_config)
        monkeypatch.setattr(xhs_runtime, "select_xhs_account", fake_select_account)
        monkeypatch.setattr(xhs_runtime, "select_xhs_proxy", fake_select_proxy)
        monkeypatch.setattr(xhs_runtime, "record_xhs_account_result", fake_record_account_result)
        monkeypatch.setattr(xhs_dao, "create_note_detail", fake_create_note_detail)
        monkeypatch.setattr(xhs_dao, "update_note_detail_tagging", fake_update_note_detail_tagging)
        monkeypatch.setattr(findings_dao, "insert_finding", fake_insert_finding)
        monkeypatch.setattr(
            InfoCollectionToolFactory,
            "create_xhs_detail_tagging_tool",
            fake_create_xhs_detail_tagging_tool,
        )
        monkeypatch.setattr(XhsPipeline, "_get_crawler", fake_get_crawler)
        monkeypatch.setattr(XhsPipeline, "_get_detail_tagging_agent", fail_old_detail_agent)
        monkeypatch.setattr(xhs_pipeline_module.random, "uniform", lambda _start, _end: 0)

        import crawler_tools.xhs_client_v2 as xhs_client_v2

        monkeypatch.setattr(xhs_client_v2, "XhsClientV2", _Client)

        pipeline = XhsPipeline(_FakeDB(), object())
        result = await pipeline._stage_detail_tagging(
            project_id="project-1",
            suspicious_notes=[
                {
                    "note_id": "note-1",
                    "task_id": "task-1",
                    "_sub_task_id": "task-1_xhs_0",
                    "xsec_token": "token-1",
                    "xsec_source": "pc_feed",
                    "user": {"user_id": "user-1", "nickname": "用户1"},
                },
                {
                    "note_id": "note-2",
                    "task_id": "task-1",
                    "_sub_task_id": "task-1_xhs_1",
                    "xsec_token": "token-2",
                    "xsec_source": "pc_feed",
                    "user": {"user_id": "user-2", "nickname": "用户2"},
                },
                {
                    "note_id": "note-3",
                    "task_id": "task-1",
                    "_sub_task_id": "task-1_xhs_2",
                    "xsec_token": "token-3",
                    "xsec_source": "pc_feed",
                    "user": {"user_id": "user-3", "nickname": "用户3"},
                },
            ],
            enable_comments=False,
            enable_images=True,
        )

        assert result["images_count"] == 3
        assert result["detail_count"] == 3
        assert result["detail_findings_count"] == 3
        assert {item["note_id"] for item in stored["details"]} == {"note-1", "note-2", "note-3"}
        assert [item["tagging"]["attention_score"] for item in stored["detail_tagging"]] == [82, 82, 82]
        assert {finding["xhs_user_id"] for finding in stored["findings"]} == {"user-1", "user-2", "user-3"}
        assert {request.context["content"] for request in tool.requests} == {
            "detail:note-1",
            "detail:note-2",
            "detail:note-3",
        }
        assert max_active > 1

    asyncio.run(_run())


def test_xhs_detail_tool_leases_runtime_account_and_proxy():
    async def _run():
        from api.services.xhs_runtime import XhsAccountLease, XhsProxyLease

        calls = {
            "accounts": [],
            "proxies": [],
            "clients": [],
            "records": [],
        }

        async def runtime_config_loader():
            return {
                "account_pool": {
                    "request_interval_min_seconds": 0,
                    "request_interval_max_seconds": 0,
                },
                "proxy_pool": {"request_timeout": 12.5},
            }

        async def account_selector(db, purpose, config):
            calls["accounts"].append({"db": db, "purpose": purpose, "config": config})
            return XhsAccountLease(
                account_name="account-1",
                cookie_string="cookie-1",
                source="pool:test",
            )

        async def proxy_selector(config):
            calls["proxies"].append(config)
            return XhsProxyLease(proxy_url="http://user:pass@127.0.0.1:8080", source="static")

        async def result_recorder(db, account_name, **kwargs):
            calls["records"].append({"db": db, "account_name": account_name, **kwargs})

        class _Client:
            def __init__(self, cookie_string, proxy_url=None, request_timeout=30.0) -> None:
                calls["clients"].append({
                    "cookie_string": cookie_string,
                    "proxy_url": proxy_url,
                    "request_timeout": request_timeout,
                })
                self.closed = False

            async def pong(self):
                return True

            async def get_note_by_id(self, note_id, xsec_token="", xsec_source=""):
                return {
                    "desc": f"detail:{note_id}:{xsec_token}:{xsec_source}",
                    "image_list": [{"url_default": "https://img.example/a.jpg"}],
                }

            async def get_note_all_comments(
                self,
                note_id,
                xsec_token="",
                max_count=20,
                crawl_interval=0.5,
            ):
                return [{
                    "content": f"comment:{note_id}:{max_count}:{crawl_interval}",
                    "user_info": {"nickname": "评论用户"},
                    "create_time": 1767225600000,
                }]

            async def close(self):
                self.closed = True

        db = _FakeDB()
        tool = XhsDetailTool(
            db=db,
            runtime_config_loader=runtime_config_loader,
            account_selector=account_selector,
            proxy_selector=proxy_selector,
            result_recorder=result_recorder,
            client_factory=_Client,
        )

        result = await tool.fetch_detail(
            DetailRequest(
                source="xhs",
                item_id="note-1",
                project_id="project-1",
                task_id="task-1",
                xsec_token="xsec-token",
                xsec_source="pc_feed",
                options={"enable_comments": True, "max_comments": 3},
            )
        )
        await tool.close()

        assert result.ok
        assert result.content == "detail:note-1:xsec-token:pc_feed"
        assert result.images_urls == ["https://img.example/a.jpg"]
        assert len(result.comments_data) == 1
        assert "评论用户" in result.comments_summary
        assert result.meta["used_v2"] is True
        assert result.meta["account_name"] == "account-1"
        assert result.meta["proxy"]["proxy_url"] == "http://user:***@127.0.0.1:8080"
        assert calls["accounts"][0]["purpose"] == "detail"
        assert calls["clients"] == [{
            "cookie_string": "cookie-1",
            "proxy_url": "http://user:pass@127.0.0.1:8080",
            "request_timeout": 12.5,
        }]
        assert [record["success"] for record in calls["records"]] == [True, True]

    asyncio.run(_run())


def test_xhs_pipeline_run_pipeline_uses_streaming_toolset(monkeypatch):
    async def _run():
        from api.dao import findings as findings_dao
        from api.dao import xhs as xhs_dao
        from api.services import xhs_pipeline as xhs_pipeline_module
        from api.services.info_collection.factory import InfoCollectionToolFactory
        from api.services.xhs_pipeline import XhsPipeline

        stored = {
            "tasks": [],
            "notes": [],
            "note_tagging": [],
            "details": [],
            "detail_tagging": [],
            "findings": [],
            "profiles": [],
        }

        async def fake_update_search_task(db, task_id, patch):
            stored["tasks"].append({"task_id": task_id, "patch": dict(patch)})
            return {"_id": task_id, **patch}

        async def fake_create_notes_batch(db, notes):
            stored["notes"].extend([dict(note) for note in notes])
            return notes

        async def fake_update_note_tagging(db, note_id, tagging):
            stored["note_tagging"].append({"note_id": note_id, "tagging": tagging})

        async def fake_create_note_detail(db, **kwargs):
            stored["details"].append(dict(kwargs))

        async def fake_update_note_detail_tagging(db, note_id, tagging):
            stored["detail_tagging"].append({"note_id": note_id, "tagging": tagging})

        async def fake_insert_finding(db, finding):
            stored["findings"].append(dict(finding))

        monkeypatch.setattr(xhs_dao, "update_search_task", fake_update_search_task)
        monkeypatch.setattr(xhs_dao, "create_notes_batch", fake_create_notes_batch)
        monkeypatch.setattr(xhs_dao, "update_note_tagging", fake_update_note_tagging)
        monkeypatch.setattr(xhs_dao, "create_note_detail", fake_create_note_detail)
        monkeypatch.setattr(xhs_dao, "update_note_detail_tagging", fake_update_note_detail_tagging)
        monkeypatch.setattr(findings_dao, "insert_finding", fake_insert_finding)

        class _SearchTool:
            name = "xhs_search"

            async def search(self, request):
                assert request.options["sort_type"] == "general"
                items = [{
                    "note_id": "note-1",
                    "project_id": request.project_id,
                    "task_id": request.task_id,
                    "title": "目标公司笔记",
                    "user": {"user_id": "user-1", "nickname": "测试用户"},
                    "xsec_token": "token-1",
                    "xsec_source": "pc_feed",
                }]
                stored["notes"].extend([dict(item) for item in items])
                return SearchResult(
                    source="xhs",
                    query=request.query,
                    items=items,
                )

        class _DetailTool(_FakeDetailTool):
            async def fetch_detail(self, request):
                assert request.options["enable_comments"] is True
                assert request.options["enable_images"] is False
                assert request.options["max_comments"] == 5
                return DetailResult(
                    source="xhs",
                    item_id=request.item_id,
                    content="detail-content",
                    raw={"desc": "detail-content"},
                    comments_summary="comment-summary",
                    comments_data=[{"content": "comment"}],
                    images_urls=[],
                )

            async def close(self):
                return None

        class _Toolset:
            profile_tool = None

            def __init__(self):
                self.search_tool = _SearchTool()
                self.detail_tool = _DetailTool()
                self.note_tagging_tool = _FakeTaggingTool("note", score=90)
                self.detail_tagging_tool = _FakeTaggingTool(
                    "detail",
                    score=85,
                    findings=[{"type": "personal_info", "value": "疑似员工"}],
                )

            def state(self):
                return {
                    "xhs_search_tool": self.search_tool,
                    "xhs_detail_tool": self.detail_tool,
                    "xhs_note_tagging_tool": self.note_tagging_tool,
                    "xhs_detail_tagging_tool": self.detail_tagging_tool,
                }

            async def close(self):
                await self.detail_tool.close()

        async def fake_create_xhs_toolset(self, pipeline_owner):
            return _Toolset()

        monkeypatch.setattr(
            InfoCollectionToolFactory,
            "create_xhs_toolset",
            fake_create_xhs_toolset,
        )

        async def fail_old_note_tagging(self, notes, keyword=""):
            raise AssertionError("旧串行笔记打标阶段不应被 run_pipeline 调用")

        async def fail_old_detail_tagging(self, *args, **kwargs):
            raise AssertionError("旧串行详情阶段不应被 run_pipeline 调用")

        async def fake_profile_generation(self, task_id, project_id, keyword="", **kwargs):
            stored["profiles"].append({
                "task_id": task_id,
                "project_id": project_id,
                "keyword": keyword,
            })
            return [{"user_id": "user-1"}]

        monkeypatch.setattr(XhsPipeline, "_stage_note_tagging", fail_old_note_tagging)
        monkeypatch.setattr(XhsPipeline, "_stage_detail_tagging", fail_old_detail_tagging)
        monkeypatch.setattr(XhsPipeline, "_stage_profile_generation", fake_profile_generation)
        monkeypatch.setattr(xhs_pipeline_module, "obs_log", lambda *args, **kwargs: None)

        pipeline = XhsPipeline(_FakeDB(), object())
        result = await pipeline.run_pipeline(
            task_id="task-1",
            project_id="project-1",
            keyword="目标公司",
            max_notes=10,
            attention_threshold=60,
            sort_type="general",
            enable_comments=True,
            enable_images=False,
            max_comments=5,
        )

        assert result["notes_count"] == 1
        assert result["suspicious_count"] == 1
        assert result["profiles_count"] == 1
        assert result["comments_count"] == 1
        assert result["images_count"] == 0
        assert result["detail_findings_count"] == 1
        assert stored["notes"][0]["note_id"] == "note-1"
        assert stored["note_tagging"][0]["tagging"]["attention_score"] == 90
        assert stored["details"][0]["comments_summary"] == "comment-summary"
        assert stored["detail_tagging"][0]["tagging"]["attention_score"] == 85
        assert stored["findings"][0]["xhs_user_id"] == "user-1"
        assert stored["profiles"] == [{
            "task_id": "task-1",
            "project_id": "project-1",
            "keyword": "目标公司",
        }]
        assert stored["tasks"][0]["patch"]["status"] == "running"
        assert stored["tasks"][-1]["patch"]["status"] == "completed"

    asyncio.run(_run())
