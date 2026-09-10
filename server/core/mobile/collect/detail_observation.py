"""Observability projection for the mobile detail stage."""
from __future__ import annotations

from typing import Any, Callable


class DetailStageObserver:
    def __init__(self, observer: Callable[..., Any]) -> None:
        self.observer = observer

    def enter(self, run: Any) -> None:
        self.observer(
            f"点进详情深采 score={run.candidate.get('score')}",
            **self._base(run),
            level="info",
            event="collect_detail_enter",
            data={
                "keyword": run.keyword,
                "score": run.candidate.get("score"),
                "subject_match": run.candidate.get("subject_match"),
                "tap": [run.tap_x, run.tap_y],
                "preview_fields": run.candidate.get("fields"),
            },
        )

    def link(self, run: Any, ok: bool, result: Any) -> None:
        self.observer(
            "详情页原文链接提取成功"
            if ok
            else f"详情页原文链接提取失败: {result.error}",
            **self._base(run),
            level="info" if ok else "warning",
            event=(
                "collect_source_link_extracted"
                if ok
                else "collect_source_link_error"
            ),
            data={
                "keyword": run.keyword,
                "strategy": run.source_link_strategy,
                **({"url": run.source_url} if ok else {"error": result.error}),
                "elapsed_ms": result.elapsed_ms,
            },
        )

    def source_ready(self, run: Any, result: dict[str, Any]) -> None:
        self.observer(
            "公众号原文已由浏览器池完整读取，跳过手机详情滚动",
            **self._base(run),
            level="notice",
            event="collect_source_document_ready",
            data={
                "keyword": run.keyword,
                "document_id": result.get("document_id"),
                "version_id": result.get("version_id"),
                "cached": result.get("cached"),
                "images": result.get("image_count"),
                "screenshots": result.get("screenshot_count"),
            },
        )

    def rejected(
        self,
        run: Any,
        result: dict[str, Any],
        record_ids: list[str],
    ) -> None:
        self.observer(
            "公众号原文未通过独立相关性审核，已丢弃本次关联",
            **self._base(run),
            level="info",
            event="collect_source_document_rejected",
            data={
                "keyword": run.keyword,
                "url": result.get("source_url") or run.source_url,
                "document_id": result.get("document_id"),
                "version_id": result.get("version_id"),
                "subject_match": result.get("subject_match"),
                "article_scope": result.get("article_scope"),
                "review_decision": result.get("review_decision"),
                "required_subject_match": result.get("required_subject_match"),
                "reason": result.get("score_reason") or result.get("reason"),
                "archived_record_ids": record_ids,
            },
        )

    def ingest_fallback(self, run: Any, error: Exception) -> None:
        run.ctx.logger.warning(
            "[collect] 来源文档浏览器读取失败，回退手机深采: %s", error
        )
        self.observer(
            f"来源文档读取失败，回退手机深采: {error}",
            **self._base(run),
            level="warning",
            event="collect_source_document_fallback",
            data={
                "keyword": run.keyword,
                "url": run.source_url,
                "error": str(error),
            },
        )

    def scroll(self, run: Any, swipes: int, reached_bottom: bool) -> None:
        suffix = "(到底)" if reached_bottom else "(达上限)"
        self.observer(
            f"详情页滑动 {swipes} 次{suffix} 截图 {len(run.shots_b64)} 张",
            **self._base(run),
            level="info",
            event="collect_detail_scroll",
            data={
                "keyword": run.keyword,
                "swipes": swipes,
                "shots": len(run.shots_b64),
                "reached_bottom": reached_bottom,
            },
        )

    def error(self, run: Any, error: Exception) -> None:
        run.ctx.logger.warning(
            "[collect] 详情深采失败 kw=%r: %s", run.keyword, error
        )
        self.observer(
            f"详情深采失败: {error}",
            **self._base(run),
            level="warning",
            event="collect_detail_error",
            data={"keyword": run.keyword, "error": str(error)},
        )

    @staticmethod
    def _base(run: Any) -> dict[str, str]:
        return {
            "project_id": str(run.shared.get("project_id") or ""),
            "task_id": str(run.shared["run_task_id"]),
            "source": "mobile_collect",
        }
