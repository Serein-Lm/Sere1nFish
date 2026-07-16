"""Reusable streaming stages for XHS information collection."""

from __future__ import annotations

from typing import Any

from core.stream import Item, RetryPolicy, Stage


class XhsSearchStage(Stage):
    """Search one keyword and emit normalized notes to tagging workers."""

    name = "search"
    retry = RetryPolicy(max_attempts=1)

    def __init__(
        self,
        *,
        concurrency: int,
        project_id: str,
        task_id: str,
        per_keyword: int,
        db: Any,
        pipeline_owner: Any,
        sort_type: str = "time_descending",
        target_id: str = "",
        target_name: str = "",
    ) -> None:
        self.project_id = project_id
        self.task_id = task_id
        self.per_keyword = per_keyword
        self.db = db
        self.pipeline_owner = pipeline_owner
        self.sort_type = sort_type
        self.target_id = target_id
        self.target_name = target_name
        super().__init__(concurrency=concurrency)

    async def handle(self, item: Item, ctx) -> None:
        from api.services.info_collection import SearchRequest

        keyword = item.payload
        idx = item.meta.get("idx", 0)
        total = item.meta.get("total", 1)
        sub_task_id = f"{self.task_id}_xhs_{idx}"
        search_tool = ctx.state.get("xhs_search_tool")

        ctx.logger.info(f"[xhs-stream] 搜索开始 [{idx+1}/{total}] keyword='{keyword}'")
        if not search_tool:
            raise RuntimeError("xhs_search_tool 未初始化")

        search_result = await search_tool.search(
            SearchRequest(
                source="xhs",
                query=keyword,
                project_id=self.project_id,
                task_id=sub_task_id,
                limit=self.per_keyword,
                options={
                    "sort_type": self.sort_type,
                    "target_id": self.target_id,
                    "target_name": self.target_name,
                },
            )
        )
        notes = search_result.items

        ctx.state["all_notes_count"] = ctx.state.get("all_notes_count", 0) + len(notes)
        ctx.logger.info(f"[xhs-stream] 搜索完成 [{idx+1}/{total}] keyword='{keyword}' notes={len(notes)}")
        for note in notes:
            note["_keyword"] = keyword
            note["_sub_task_id"] = sub_task_id
            await ctx.emit("tagging", note)


class XhsTaggingStage(Stage):
    """Tag one XHS note and emit suspicious notes to detail workers."""

    name = "tagging"
    retry = RetryPolicy(max_attempts=1)

    def __init__(
        self,
        *,
        concurrency: int,
        attention_threshold: int,
        db: Any,
        pipeline_owner: Any,
    ) -> None:
        self.attention_threshold = attention_threshold
        self.db = db
        self.pipeline_owner = pipeline_owner
        super().__init__(concurrency=concurrency)

    async def on_setup(self, state: dict[str, Any]) -> None:
        state.setdefault("tagging_count", 0)
        state.setdefault("all_suspicious_count", 0)

    async def handle(self, item: Item, ctx) -> None:
        from api.dao import xhs as xhs_dao
        from api.services.info_collection import TagRequest

        note = item.payload
        note_id = note.get("note_id", "")
        keyword = note.get("_keyword", "")
        tagging_tool = ctx.state.get("xhs_note_tagging_tool")

        ctx.state["tagging_count"] = ctx.state.get("tagging_count", 0) + 1
        try:
            if not tagging_tool:
                raise RuntimeError("xhs_note_tagging_tool 未初始化")
            tag_result = await tagging_tool.tag(
                TagRequest(
                    source="xhs",
                    kind="note",
                    item_id=note_id,
                    item=note,
                    project_id=note.get("project_id", ""),
                    task_id=note.get("_sub_task_id", note.get("task_id", "")),
                    context={"keyword": keyword},
                )
            )
            tagging = tag_result.tagging
            if tagging:
                await xhs_dao.update_note_tagging(self.db, note_id, tagging)
                if tag_result.score >= self.attention_threshold:
                    ctx.state["all_suspicious_count"] = ctx.state.get("all_suspicious_count", 0) + 1
                    note["_tagging"] = tagging
                    await ctx.emit("detail", note)
            else:
                await xhs_dao.update_note_tagging(self.db, note_id, {
                    "is_suspicious": False,
                    "attention_score": 0,
                    "attack_surface_types": [],
                    "reason": "打标解析失败",
                })
        except Exception as exc:
            ctx.logger.warning(f"[xhs-stream] 打标失败 note={note_id}: {exc}")
            await xhs_dao.update_note_tagging(self.db, note_id, {
                "is_suspicious": False,
                "attention_score": 0,
                "reason": f"打标失败: {exc}",
            })

        if ctx.state["tagging_count"] % 10 == 0:
            ctx.logger.info(
                f"[xhs-stream] 打标进度: {ctx.state['tagging_count']} 条, "
                f"可疑: {ctx.state.get('all_suspicious_count', 0)}"
            )


class XhsNoteTaggingPersistStage(Stage):
    """Tag existing XHS notes and persist tagging without emitting downstream work."""

    name = "note_tagging_persist"
    retry = RetryPolicy(max_attempts=1)

    def __init__(self, *, concurrency: int, db: Any, keyword: str = "") -> None:
        self.db = db
        self.keyword = keyword
        super().__init__(concurrency=concurrency)

    async def on_setup(self, state: dict[str, Any]) -> None:
        state.setdefault("tagging_count", 0)

    async def handle(self, item: Item, ctx) -> None:
        from api.dao import xhs as xhs_dao
        from api.services.info_collection import TagRequest

        note = item.payload
        note_id = note.get("note_id", "")
        keyword = note.get("_keyword") or self.keyword
        tagging_tool = ctx.state.get("xhs_note_tagging_tool")
        ctx.state["tagging_count"] = ctx.state.get("tagging_count", 0) + 1

        try:
            if not tagging_tool:
                raise RuntimeError("xhs_note_tagging_tool 未初始化")
            tag_result = await tagging_tool.tag(
                TagRequest(
                    source="xhs",
                    kind="note",
                    item_id=note_id,
                    item=note,
                    project_id=note.get("project_id", ""),
                    task_id=note.get("_sub_task_id", note.get("task_id", "")),
                    context={"keyword": keyword},
                )
            )
            if tag_result.tagging:
                await xhs_dao.update_note_tagging(self.db, note_id, tag_result.tagging)
        except Exception as exc:
            ctx.logger.warning(f"[xhs-stream] 兼容笔记打标失败 note={note_id}: {exc}")
            await xhs_dao.update_note_tagging(self.db, note_id, {
                "is_suspicious": False,
                "attention_score": 0,
                "attack_surface_types": [],
                "reason": f"打标失败: {exc}",
                "key_info_extracted": [],
            })


async def _insert_detail_findings(
    *,
    db: Any,
    project_id: str,
    note: dict[str, Any],
    note_id: str,
    detail_tagging: dict[str, Any],
) -> int:
    from api.dao import findings as findings_dao

    detail_score = detail_tagging.get("attention_score", 0)
    try:
        detail_score = int(detail_score)
    except Exception:
        detail_score = 0
    user_info = note.get("user", {})
    user_id = user_info.get("user_id", "")
    if detail_score < 60 or not user_id:
        return 0

    import uuid as _uuid

    nickname = user_info.get("nickname", "")
    detail_findings = detail_tagging.get("findings", [])
    if not isinstance(detail_findings, list):
        detail_findings = []

    inserted = 0
    for detail_finding in detail_findings:
        if not isinstance(detail_finding, dict):
            continue
        finding_doc = {
            "finding_id": _uuid.uuid4().hex[:12],
            "project_id": project_id,
            "task_id": note.get("_sub_task_id", note.get("task_id", "")),
            **(
                {
                    "target_id": note.get("target_id"),
                    "target_name": note.get("target_name", ""),
                }
                if note.get("target_id")
                else {}
            ),
            "source": "xhs",
            "type": detail_finding.get("type", "other"),
            "channel": "xhs_note_detail",
            "label": str(detail_finding.get("value", ""))[:80],
            "value": nickname,
            "url": f"https://www.xiaohongshu.com/explore/{note_id}",
            "xhs_user_id": user_id,
            "xhs_note_ids": [note_id],
            "note_id": note_id,
            "attention_score": detail_score,
            "attention_reason": detail_finding.get("attention_reason", ""),
            "context": detail_finding.get("evidence", ""),
            "evidence": detail_tagging.get("summary", ""),
        }
        await findings_dao.insert_finding(db, finding_doc)
        inserted += 1
    return inserted


class XhsPrefetchedDetailTaggingStage(Stage):
    """Persist prefetched XHS detail and tag it through the detail tagging tool."""

    name = "prefetched_detail_tagging"
    retry = RetryPolicy(max_attempts=1)

    def __init__(self, *, concurrency: int, project_id: str, db: Any) -> None:
        self.project_id = project_id
        self.db = db
        super().__init__(concurrency=concurrency)

    async def on_setup(self, state: dict[str, Any]) -> None:
        state.setdefault("detail_count", 0)
        state.setdefault("detail_findings_count", 0)

    async def handle(self, item: Item, ctx) -> None:
        from api.dao import xhs as xhs_dao
        from api.services.info_collection import TagRequest

        payload = item.payload
        note = payload["note"]
        note_id = note.get("note_id", "")
        content = payload.get("content", "")
        comments_summary = payload.get("comments_summary", "")
        comments_data = payload.get("comments_data", [])
        images_urls = payload.get("images_urls", [])
        tagging_tool = ctx.state.get("xhs_detail_tagging_tool")
        if not tagging_tool:
            raise RuntimeError("xhs_detail_tagging_tool 未初始化")

        await xhs_dao.create_note_detail(
            self.db,
            note_id=note_id,
            project_id=self.project_id,
            content=content,
            comments_summary=comments_summary,
            comments_data=comments_data,
            images_urls=images_urls,
            xsec_token=note.get("xsec_token", ""),
            xsec_source=note.get("xsec_source", ""),
        )
        ctx.state["detail_count"] = ctx.state.get("detail_count", 0) + 1
        tag_result = await tagging_tool.tag(
            TagRequest(
                source="xhs",
                kind="detail",
                item_id=note_id,
                item=note,
                project_id=self.project_id,
                task_id=note.get("_sub_task_id", note.get("task_id", "")),
                context={
                    "content": content,
                    "comments_summary": comments_summary,
                },
            )
        )
        detail_tagging = tag_result.tagging
        if detail_tagging:
            await xhs_dao.update_note_detail_tagging(self.db, note_id, detail_tagging)
            inserted = await _insert_detail_findings(
                db=self.db,
                project_id=self.project_id,
                note=note,
                note_id=note_id,
                detail_tagging=detail_tagging,
            )
            ctx.state["detail_findings_count"] = ctx.state.get("detail_findings_count", 0) + inserted


class XhsDetailStage(Stage):
    """Fetch one XHS note detail, tag it, and persist high-score findings."""

    name = "detail"
    retry = RetryPolicy(max_attempts=1)

    def __init__(
        self,
        *,
        concurrency: int,
        project_id: str,
        db: Any,
        pipeline_owner: Any,
        enable_comments: bool = False,
        enable_images: bool = True,
        max_comments: int = 20,
    ) -> None:
        self.project_id = project_id
        self.db = db
        self.pipeline_owner = pipeline_owner
        self.enable_comments = enable_comments
        self.enable_images = enable_images
        self.max_comments = max_comments
        super().__init__(concurrency=concurrency)

    async def on_setup(self, state: dict[str, Any]) -> None:
        state.setdefault("detail_count", 0)
        state.setdefault("detail_findings_count", 0)
        state.setdefault("comments_count", 0)
        state.setdefault("images_count", 0)

    async def handle(self, item: Item, ctx) -> None:
        from api.dao import xhs as xhs_dao
        from api.services.info_collection import DetailRequest, TagRequest

        note = item.payload
        note_id = note.get("note_id", "")
        xsec_token = note.get("xsec_token", "")
        xsec_source = note.get("xsec_source", "")
        detail_tool = ctx.state.get("xhs_detail_tool")
        tagging_tool = ctx.state.get("xhs_detail_tagging_tool")

        try:
            if not detail_tool:
                raise RuntimeError("xhs_detail_tool 未初始化")
            if not tagging_tool:
                raise RuntimeError("xhs_detail_tagging_tool 未初始化")
            detail_result = await detail_tool.fetch_detail(
                DetailRequest(
                    source="xhs",
                    item_id=note_id,
                    project_id=self.project_id,
                    task_id=note.get("_sub_task_id", ""),
                    xsec_token=xsec_token,
                    xsec_source=xsec_source,
                    options={
                        "enable_comments": self.enable_comments,
                        "enable_images": self.enable_images,
                        "max_comments": self.max_comments,
                    },
                )
            )
            if not detail_result.ok:
                ctx.logger.warning(f"[xhs-stream] 详情获取失败 note={note_id}")
                return

            await xhs_dao.create_note_detail(
                self.db,
                note_id=note_id,
                project_id=self.project_id,
                content=detail_result.content,
                comments_summary=detail_result.comments_summary,
                comments_data=detail_result.comments_data,
                images_urls=detail_result.images_urls,
                xsec_token=xsec_token,
                xsec_source=xsec_source,
            )
            ctx.state["detail_count"] = ctx.state.get("detail_count", 0) + 1
            ctx.state["comments_count"] = ctx.state.get("comments_count", 0) + len(detail_result.comments_data)
            ctx.state["images_count"] = ctx.state.get("images_count", 0) + len(detail_result.images_urls)
            tag_result = await tagging_tool.tag(
                TagRequest(
                    source="xhs",
                    kind="detail",
                    item_id=note_id,
                    item=note,
                    project_id=self.project_id,
                    task_id=note.get("_sub_task_id", ""),
                    context={
                        "content": detail_result.content,
                        "comments_summary": detail_result.comments_summary,
                    },
                )
            )
            detail_tagging = tag_result.tagging
            if detail_tagging:
                await xhs_dao.update_note_detail_tagging(self.db, note_id, detail_tagging)
                inserted = await self._insert_detail_findings(note, note_id, detail_tagging)
                ctx.state["detail_findings_count"] = ctx.state.get("detail_findings_count", 0) + inserted
            ctx.logger.info(f"[xhs-stream] 详情+打标完成 note={note_id}")
        except Exception as exc:
            ctx.logger.warning(f"[xhs-stream] 详情处理失败 note={note_id}: {exc}")

    async def _insert_detail_findings(
        self,
        note: dict[str, Any],
        note_id: str,
        detail_tagging: dict[str, Any],
    ) -> int:
        return await _insert_detail_findings(
            db=self.db,
            project_id=self.project_id,
            note=note,
            note_id=note_id,
            detail_tagging=detail_tagging,
        )
