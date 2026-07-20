"""来源文档版本、Target 聚类与联系方式证据的纯逻辑回归测试。"""


def _capture(*, raw_html: bytes, rendered_html: bytes):
    from api.services.source_documents.contracts import (
        CapturedDocument,
        CapturedImage,
    )

    return CapturedDocument(
        source_type="wechat_article",
        canonical_url="https://mp.weixin.qq.com/s/article-id",
        requested_url="https://mp.weixin.qq.com/s/article-id?scene=1",
        title="测试文章",
        account="测试公众号",
        publish_time="2026-07-16",
        text="联系人张三，手机 13800138000。",
        raw_html=raw_html,
        rendered_html=rendered_html,
        images=[
            CapturedImage(
                index=0,
                source_url="https://mmbiz.qpic.cn/image-1",
                data=b"image-one",
                content_type="image/jpeg",
            )
        ],
    )


def test_wechat_canonical_url_discards_tracking_query_and_fragment():
    from api.services.source_documents.urls import canonicalize_source_url

    canonical = canonicalize_source_url(
        "http://MP.WEIXIN.QQ.COM/s/article-id?scene=1&from=timeline#wechat_redirect"
    )
    assert canonical == "https://mp.weixin.qq.com/s/article-id"


def test_stable_content_hash_ignores_dynamic_page_shell():
    from api.dao.source_documents import (
        document_id_for_url,
        version_id_for_content,
    )
    from api.services.source_documents.analysis import stable_content_hash

    first = _capture(raw_html=b"token=one", rendered_html=b"session=one")
    second = _capture(raw_html=b"token=two", rendered_html=b"session=two")

    assert stable_content_hash(first) == stable_content_hash(second)

    document_id = document_id_for_url(first.canonical_url)
    assert version_id_for_content(
        document_id, stable_content_hash(first)
    ) == version_id_for_content(document_id, stable_content_hash(second))


def test_stable_hash_uses_declared_images_when_a_download_is_partial():
    from api.services.source_documents.analysis import stable_content_hash

    complete = _capture(raw_html=b"raw-one", rendered_html=b"dom-one")
    complete.metadata["image_urls"] = ["https://mmbiz.qpic.cn/image-1"]
    partial = _capture(raw_html=b"raw-two", rendered_html=b"dom-two")
    partial.metadata["image_urls"] = ["https://mmbiz.qpic.cn/image-1"]
    partial.images = []

    assert stable_content_hash(complete) == stable_content_hash(partial)


def test_source_document_identity_prevents_field_change_duplicates():
    from api.dao.mobile_collect import stable_record_id

    first = stable_record_id(
        "task-1",
        {"title": "旧标题"},
        ["title"],
        source_document_id="doc-1",
    )
    second = stable_record_id(
        "task-1",
        {"title": "新标题", "summary": "新结构化结果"},
        ["title"],
        source_document_id="doc-1",
    )
    assert first == second


def test_target_identity_normalizes_common_name_separators():
    from api.dao.targets import normalize_target_name, target_id_for_name

    assert normalize_target_name(" 天津-滨海 国际机场（集团） ") == "天津滨海国际机场集团"
    assert target_id_for_name("天津-滨海 国际机场") == target_id_for_name(
        "天津滨海国际机场"
    )


def test_contact_extraction_preserves_local_evidence_context():
    from core.mobile.collect.contacts import extract_contacts

    text = (
        "项目报名要求如下。\n"
        "商务联系人：张三，手机 13800138000，负责投标资料接收。\n"
        "其他事项请查看原文。"
    )
    contacts = extract_contacts(text)

    phone = next(item for item in contacts if item["channel"] == "phone")
    assert phone["value"] == "13800138000"
    assert "张三" in phone["context"]
    assert "投标资料接收" in phone["context"]
    assert phone["contexts"] == [phone["context"]]


def test_contextual_analysis_is_separate_from_source_version_fields():
    from api.models.mobile_collect import ExtractField
    from api.services.source_documents.service import (
        _analysis_fingerprint,
        _compact_contextual_analysis,
        _source_analysis,
    )

    capture = _capture(raw_html=b"raw", rendered_html=b"dom")
    source_analysis = _source_analysis(
        capture,
        contacts=[],
        image_analysis=[],
    )
    assert source_analysis["scope"] == "source"
    assert "target_id" not in source_analysis
    assert "content" not in source_analysis["fields"]

    compact = _compact_contextual_analysis(
        {
            "fields": {
                "title": "文章标题",
                "content": "完整正文",
                "bid_deadline": "2026-08-01",
            },
            "score": 90,
        }
    )
    assert compact["fields"] == {"bid_deadline": "2026-08-01"}

    fields = [ExtractField(name="bid_deadline", description="截止时间")]
    first = _analysis_fingerprint(
        version_id="version-1",
        target_id="target-1",
        target_name="公司甲",
        keyword="招标",
        fields=fields,
    )
    second = _analysis_fingerprint(
        version_id="version-1",
        target_id="target-2",
        target_name="公司乙",
        keyword="招标",
        fields=fields,
    )
    assert first != second


def test_contextual_analysis_fingerprint_tracks_prompt_content(monkeypatch):
    from api.models.mobile_collect import ExtractField
    from api.services.source_documents import service

    fields = [ExtractField(name="summary", description="摘要")]
    monkeypatch.setattr(
        service,
        "article_analysis_prompt_fingerprint",
        lambda: "prompt-version-one",
    )
    first = service._analysis_fingerprint(
        version_id="version-1",
        target_id="target-1",
        target_name="公司甲",
        keyword="招标",
        fields=fields,
    )
    monkeypatch.setattr(
        service,
        "article_analysis_prompt_fingerprint",
        lambda: "prompt-version-two",
    )
    second = service._analysis_fingerprint(
        version_id="version-1",
        target_id="target-1",
        target_name="公司甲",
        keyword="招标",
        fields=fields,
    )

    assert first != second


def test_source_document_prompts_reject_multi_entity_roundups():
    from Sere1nGraph.graph.prompts.loader import load_prompt
    from api.services.source_documents.analysis import (
        ARTICLE_ANALYSIS_PROMPT_SLUG,
        RELEVANCE_REVIEW_PROMPT_SLUG,
    )

    extraction_prompt = load_prompt(ARTICLE_ANALYSIS_PROMPT_SLUG)
    review_prompt = load_prompt(RELEVANCE_REVIEW_PROMPT_SLUG)

    assert "multi_entity_roundup" in extraction_prompt
    assert "不得超过 39" in extraction_prompt
    assert "目标只是其中一个条目" in review_prompt
    assert "此类必须 `reject`" in review_prompt
    assert "独立相关性审核员" in review_prompt


def test_article_scope_caps_prevent_single_roundup_item_from_passing():
    from api.services.source_documents.analysis import apply_article_scope_cap

    assert apply_article_scope_cap(98, "target_focused") == 98
    assert apply_article_scope_cap(98, "multi_entity_roundup") == 39
    assert apply_article_scope_cap(98, "incidental") == 20
    assert apply_article_scope_cap(98, "unknown") == 69


def test_relevance_review_requires_contact_attribution_agreement():
    from api.services.source_documents.analysis import apply_relevance_review

    capture = _capture(raw_html=b"raw", rendered_html=b"dom")
    draft = {
        "fields": {"summary": "目标项目联系人"},
        "score": 95,
        "subject_match": 96,
        "article_scope": "target_focused",
        "target_contact_values": ["13800138000"],
    }
    review = {
        "decision": "accept",
        "article_scope": "target_focused",
        "score": 92,
        "subject_match": 94,
        "summary": "文章主要介绍目标项目。",
        "target_contact_values": [],
        "reason": "正文聚焦目标，但未确认联系方式归属。",
    }

    result = apply_relevance_review(capture, draft, review)

    assert result["review_decision"] == "accept"
    assert result["score"] == 92
    assert result["subject_match"] == 94
    assert result["target_contacts"] == []
    assert result["fields"]["contact"] == ""


def test_target_contact_filter_can_restore_dual_agent_declared_wechat_name():
    from core.mobile.collect.contacts import extract_contacts
    from api.services.source_documents.analysis import filter_target_contacts

    text = "申请方式：微信：添加官方微信号 AHTV文体中心，备注拍客申请。"

    contacts = filter_target_contacts(
        extract_contacts(text),
        ["AHTV文体中心"],
        text=text,
    )

    assert contacts == [
        {
            "channel": "wechat",
            "value": "AHTV文体中心",
            "label": "微信号: AHTV文体中心",
            "context": text,
            "contexts": [text],
            "source": "text",
            "attribution": "dual_agent_declared",
        }
    ]


def test_relevance_review_conservatively_merges_two_agents():
    from api.services.source_documents.analysis import apply_relevance_review

    capture = _capture(raw_html=b"raw", rendered_html=b"dom")
    draft = {
        "fields": {"summary": "错误的整篇摘要"},
        "score": 95,
        "subject_match": 95,
        "article_scope": "target_focused",
        "target_contact_values": ["13800138000"],
    }
    review = {
        "decision": "reject",
        "article_scope": "multi_entity_roundup",
        "score": 70,
        "subject_match": 39,
        "summary": "目标仅出现在行业汇总的一个条目中。",
        "target_contact_values": [],
        "reason": "文章主体为多个单位的行业汇总。",
    }

    result = apply_relevance_review(capture, draft, review)

    assert result["review_decision"] == "reject"
    assert result["article_scope"] == "multi_entity_roundup"
    assert result["score"] == 70
    assert result["subject_match"] == 39
    assert result["fields"]["summary"] == "目标仅出现在行业汇总的一个条目中。"
    assert result["target_contacts"] == []


def test_article_analysis_runs_independent_relevance_reviewer(monkeypatch):
    import asyncio

    from api.services.source_documents import analysis

    calls: list[str] = []

    async def extract(*_args, **_kwargs):
        calls.append("extract")
        return {
            "fields": {"summary": "初稿", "content": "正文"},
            "score": 96,
            "subject_match": 95,
            "article_scope": "target_focused",
            "target_contact_values": ["13800138000"],
        }

    async def review(*_args, draft_analysis, **_kwargs):
        assert draft_analysis["fields"]["summary"] == "初稿"
        calls.append("review")
        return {
            "decision": "accept",
            "article_scope": "target_focused",
            "score": 92,
            "subject_match": 93,
            "summary": "审核后的目标专属摘要",
            "target_contact_values": ["13800138000"],
            "reason": "全文聚焦目标项目。",
        }

    monkeypatch.setattr(analysis, "analyze_article_fields", extract)
    monkeypatch.setattr(analysis, "review_article_relevance", review)

    result = asyncio.run(
        analysis.analyze_and_review_article(
            _capture(raw_html=b"raw", rendered_html=b"dom"),
            fields=[],
            target_name="目标单位",
            keyword="目标单位 招标",
            required_subject_match=70,
        )
    )

    assert calls == ["extract", "review"]
    assert result["review_decision"] == "accept"
    assert result["subject_match"] == 93
    assert result["fields"]["summary"] == "审核后的目标专属摘要"
    assert result["target_contacts"][0]["value"] == "13800138000"


def test_target_review_gate_requires_accept_decision_and_threshold():
    from api.services.source_documents.service import _passes_target_review

    assert _passes_target_review(
        {"review_decision": "accept", "subject_match": 90}, 70
    )
    assert not _passes_target_review(
        {"review_decision": "reject", "subject_match": 90}, 70
    )
    assert not _passes_target_review(
        {"review_decision": "accept", "subject_match": 69}, 70
    )
    assert not _passes_target_review({"subject_match": 100}, 70)


def test_rejected_relevance_review_stops_before_source_persistence(monkeypatch):
    import asyncio

    from api.services.source_documents import service

    capture = _capture(raw_html=b"raw", rendered_html=b"dom")

    class _Provider:
        async def capture(self, *_args, **_kwargs):
            return capture

    async def get_version(*_args, **_kwargs):
        return None

    async def reject(*_args, **_kwargs):
        return {
            "fields": {"summary": "目标仅为汇总中的一个条目"},
            "score": 60,
            "subject_match": 39,
            "score_reason": "文章主体为多个单位的行业汇总。",
            "article_scope": "multi_entity_roundup",
            "review_decision": "reject",
            "target_contact_values": [],
            "target_contacts": [],
        }

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("审核拒绝后不得写入来源版本或 Target 关联")

    monkeypatch.setattr(service, "get_source_document_provider", lambda _url: _Provider())
    monkeypatch.setattr(service.source_dao, "get_version", get_version)
    monkeypatch.setattr(service, "analyze_and_review_article", reject)
    monkeypatch.setattr(service.source_dao, "begin_version", forbidden)
    monkeypatch.setattr(service.source_dao, "upsert_document", forbidden)
    monkeypatch.setattr(service.source_dao, "link_document", forbidden)

    result = asyncio.run(
        service.ingest_source_url(
            object(),
            url=capture.canonical_url,
            project_id="project-1",
            target={"target_id": "target-1", "canonical_name": "目标单位"},
            run_task_id="run-1",
            keyword="目标单位 招标",
            min_subject_match=70,
        )
    )

    assert result["ok"] is False
    assert result["rejected"] is True
    assert result["review_decision"] == "reject"
    assert result["article_scope"] == "multi_entity_roundup"


def test_source_detail_can_select_immutable_version(monkeypatch):
    import asyncio

    from api.services.source_documents import service

    async def _get_document(db, document_id):
        return {"document_id": document_id, "latest_version_id": "version-new"}

    async def _get_version(db, version_id):
        return {
            "document_id": "doc-1",
            "version_id": version_id,
            "status": "ready",
        }

    async def _get_latest_version(db, document_id):
        raise AssertionError("指定版本时不应读取 latest_version_id")

    async def _get_links(db, document_id, project_id=""):
        return [{"project_id": project_id, "document_id": document_id}]

    monkeypatch.setattr(service.source_dao, "get_document", _get_document)
    monkeypatch.setattr(service.source_dao, "get_version", _get_version)
    monkeypatch.setattr(
        service.source_dao,
        "get_latest_version",
        _get_latest_version,
    )
    monkeypatch.setattr(
        service.source_dao,
        "get_links_for_document",
        _get_links,
    )

    detail = asyncio.run(
        service.get_source_document_detail(
            object(),
            "doc-1",
            project_id="project-1",
            version_id="version-old",
        )
    )
    assert detail is not None
    assert detail["version"]["version_id"] == "version-old"


def test_source_analysis_normalizes_ratio_scores_to_percentage():
    from api.services.source_documents.analysis import clamp_score, normalize_scores

    assert normalize_scores(0.96, 1.0) == (96, 100)
    assert normalize_scores(82, 95) == (82, 95)
    assert normalize_scores("invalid", None) == (0, 0)
    assert clamp_score(101.2) == 100
    assert clamp_score("79.6") == 80
    assert clamp_score("invalid") == 0


def test_article_image_analysis_normalizes_model_importance(monkeypatch):
    import asyncio

    from api.services.source_documents import analysis
    from api.services.source_documents.contracts import CapturedImage

    async def _analyze(*args, **kwargs):
        return [
            {
                "index": 3,
                "description": "招标联系人截图",
                "visible_text": "联系电话 13800138000",
                "contacts": [],
                "is_key_evidence": True,
                "importance_score": 88.7,
                "archive_reason": "包含联系方式",
            }
        ]

    monkeypatch.setattr(analysis, "_analyze_image_batch", _analyze)
    result, error = asyncio.run(
        analysis.analyze_article_images(
            [
                CapturedImage(
                    index=3,
                    source_url="https://example.com/evidence.jpg",
                    data=b"image",
                    content_type="image/jpeg",
                )
            ]
        )
    )

    assert error == ""
    assert result[0]["importance_score"] == 89
    assert result[0]["is_key_evidence"] is True


def test_source_document_lock_serializes_waiters_and_cleans_registry():
    import asyncio

    from api.services.source_documents import service

    active = 0
    max_active = 0

    async def worker():
        nonlocal active, max_active
        async with service._hold_document_lock("doc-lock-test"):
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.005)
            active -= 1

    async def scenario():
        await asyncio.gather(*(worker() for _ in range(6)))

    asyncio.run(scenario())
    assert max_active == 1
    assert "doc-lock-test" not in service._document_locks
    assert "doc-lock-test" not in service._document_lock_users


def test_artifact_object_id_is_content_addressed_for_safe_retry():
    from api.services.source_documents.service import _artifact_object_id

    first = _artifact_object_id("version-1", "raw", b"dynamic-token-one")
    same = _artifact_object_id("version-1", "raw", b"dynamic-token-one")
    retry = _artifact_object_id("version-1", "raw", b"dynamic-token-two")

    assert first == same
    assert first != retry


def test_image_archive_policy_keeps_only_contact_and_key_evidence():
    from api.services.source_documents.contracts import CapturedImage
    from api.services.source_documents.service import _select_archive_images

    images = [
        CapturedImage(
            index=index,
            source_url=f"https://example.com/{index}.jpg",
            data=b"x",
            content_type="image/jpeg",
        )
        for index in range(4)
    ]
    selected = _select_archive_images(
        images,
        [
            {"index": 0, "importance_score": 10, "visible_text": "装饰图片"},
            {"index": 1, "importance_score": 20, "visible_text": "电话 13800138000"},
            {"index": 2, "importance_score": 90, "is_key_evidence": True},
            {"index": 3, "importance_score": 69, "visible_text": "普通配图"},
        ],
    )

    assert [image.index for image in selected] == [1, 2]
