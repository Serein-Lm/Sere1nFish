"""小红书目标选择策略。

自动模式通过数据库 Prompt 和统一 LLM 运行时判断目标是否值得采集；手动模式仅
匹配用户提供的目标名单。调用侧只消费稳定的选择结果，不感知模型或匹配细节。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from Sere1nGraph.graph.agents.runtime import create_llm
from Sere1nGraph.graph.prompts.loader import load_prompt
from core.logger import get_logger
from core.observability import observation_context


logger = get_logger("xhs_target_selection")

XHS_TARGET_SELECTION_PROMPT = "xhs_target_selection/xhs_target_selection"
XhsTargetSelectionMode = Literal["auto", "manual"]
XhsAutoTargetCategory = Literal[
    "large_enterprise",
    "internet_platform",
    "insurance_finance",
    "large_commercial_organization",
    "government",
    "public_institution",
    "public_official",
    "small_or_low_visibility",
    "other",
    "unknown",
]
XhsTargetCategory = XhsAutoTargetCategory | Literal["manual"]

_AUTO_BATCH_SIZE = 25
_AUTO_BATCH_CONCURRENCY = 3
_MANUAL_SPLIT_RE = re.compile(r"[\n\r,，;；]+")
_NAME_PUNCTUATION_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+", re.IGNORECASE)
_LEGAL_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
)
_PROHIBITED_EXISTENCE_REASON_MARKERS = (
    "疑似虚构",
    "名称错误",
    "可能不存在",
    "名称混淆",
    "名称疑似",
    "无法确认真实",
    "无法确认为真实",
)


class XhsTargetCandidate(BaseModel):
    target_id: str = Field(min_length=1)
    target_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    root_domain: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class _AiTargetDecision(BaseModel):
    target_id: str = Field(min_length=1)
    target_category: XhsAutoTargetCategory
    should_collect_xhs: bool
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> Any:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return value
        if 1 < numeric <= 100:
            return numeric / 100
        return numeric

    @field_validator("reason")
    @classmethod
    def reject_existence_judgments(cls, value: str) -> str:
        text = str(value or "").strip()
        if any(marker in text for marker in _PROHIBITED_EXISTENCE_REASON_MARKERS):
            raise ValueError("reason 禁止以目标名称真实性作为采集判断依据")
        return text


class _AiTargetDecisionBatch(BaseModel):
    decisions: list[_AiTargetDecision]


class XhsTargetDecision(BaseModel):
    target_id: str
    target_name: str
    target_category: XhsTargetCategory
    should_collect_xhs: bool
    reason: str
    confidence: float = Field(ge=0, le=1)
    source: Literal["ai", "manual", "fallback"]


class XhsTargetSelectionResult(BaseModel):
    mode: XhsTargetSelectionMode
    status: Literal["completed", "fallback"] = "completed"
    prompt_slug: str | None = None
    manual_targets: list[str] = Field(default_factory=list)
    matched_manual_targets: list[str] = Field(default_factory=list)
    unmatched_manual_targets: list[str] = Field(default_factory=list)
    decisions: list[XhsTargetDecision] = Field(default_factory=list)
    selected_count: int = 0
    skipped_count: int = 0
    error: str | None = None


class XhsTargetSelectionStrategy(Protocol):
    async def select(
        self,
        candidates: list[XhsTargetCandidate],
        *,
        project_id: str,
        task_id: str,
    ) -> XhsTargetSelectionResult: ...


def parse_manual_targets(value: Any) -> list[str]:
    """兼容前端数组、换行文本和中英文逗号文本。"""
    raw_items = value if isinstance(value, (list, tuple, set)) else [value]
    output: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        for item in _MANUAL_SPLIT_RE.split(str(raw or "")):
            text = item.strip()
            normalized = _normalize_name(text)
            if not text or not normalized or normalized in seen:
                continue
            seen.add(normalized)
            output.append(text)
    return output


def normalize_selection_mode(value: Any) -> XhsTargetSelectionMode:
    mode = str(value or "auto").strip().lower()
    if mode not in {"auto", "manual"}:
        raise ValueError("xhs_target_selection_mode 只支持 auto 或 manual")
    return mode  # type: ignore[return-value]


def _normalize_name(value: str) -> str:
    normalized = _NAME_PUNCTUATION_RE.sub("", str(value or "").strip().lower())
    for suffix in _LEGAL_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized


def _build_result(
    *,
    mode: XhsTargetSelectionMode,
    decisions: list[XhsTargetDecision],
    status: Literal["completed", "fallback"] = "completed",
    prompt_slug: str | None = None,
    manual_targets: list[str] | None = None,
    matched_manual_targets: list[str] | None = None,
    error: str | None = None,
) -> XhsTargetSelectionResult:
    manual = parse_manual_targets(manual_targets or [])
    matched = parse_manual_targets(matched_manual_targets or [])
    matched_keys = {_normalize_name(item) for item in matched}
    return XhsTargetSelectionResult(
        mode=mode,
        status=status,
        prompt_slug=prompt_slug,
        manual_targets=manual,
        matched_manual_targets=matched,
        unmatched_manual_targets=[
            item for item in manual if _normalize_name(item) not in matched_keys
        ],
        decisions=decisions,
        selected_count=sum(1 for item in decisions if item.should_collect_xhs),
        skipped_count=sum(1 for item in decisions if not item.should_collect_xhs),
        error=error,
    )


class ManualXhsTargetSelectionStrategy:
    def __init__(self, manual_targets: list[str]) -> None:
        self.manual_targets = parse_manual_targets(manual_targets)

    async def select(
        self,
        candidates: list[XhsTargetCandidate],
        *,
        project_id: str,
        task_id: str,
    ) -> XhsTargetSelectionResult:
        del project_id, task_id
        decisions: list[XhsTargetDecision] = []
        matched: list[str] = []
        for candidate in candidates:
            candidate_keys = [
                key
                for key in (
                    _normalize_name(name)
                    for name in [candidate.target_name, *candidate.aliases]
                )
                if key
            ]
            matched_name = next(
                (
                    item
                    for item in self.manual_targets
                    if _normalize_name(item) in candidate_keys
                ),
                "",
            )
            should_collect = bool(matched_name)
            if matched_name:
                matched.append(matched_name)
            decisions.append(
                XhsTargetDecision(
                    target_id=candidate.target_id,
                    target_name=candidate.target_name,
                    target_category="manual",
                    should_collect_xhs=should_collect,
                    reason=(
                        f"命中手动名单：{matched_name}"
                        if should_collect
                        else "未命中手动小红书目标名单"
                    ),
                    confidence=1,
                    source="manual",
                )
            )
        return _build_result(
            mode="manual",
            decisions=decisions,
            manual_targets=self.manual_targets,
            matched_manual_targets=matched,
        )


class AutomaticXhsTargetSelectionStrategy:
    def __init__(self, app_config: Any) -> None:
        self.app_config = app_config

    async def select(
        self,
        candidates: list[XhsTargetCandidate],
        *,
        project_id: str,
        task_id: str,
    ) -> XhsTargetSelectionResult:
        if not candidates:
            return _build_result(
                mode="auto",
                decisions=[],
                prompt_slug=XHS_TARGET_SELECTION_PROMPT,
            )

        try:
            prompt = load_prompt(XHS_TARGET_SELECTION_PROMPT)
            llm = create_llm(self.app_config, streaming=False)
            structured_llm = llm.with_structured_output(_AiTargetDecisionBatch)
        except Exception as exc:  # noqa: BLE001
            error = str(exc) or type(exc).__name__
            logger.warning("XHS 目标判定运行时不可用 task=%s: %s", task_id, error)
            return _build_result(
                mode="auto",
                decisions=[
                    XhsTargetDecision(
                        target_id=candidate.target_id,
                        target_name=candidate.target_name,
                        target_category="unknown",
                        should_collect_xhs=False,
                        reason="AI 目标判定不可用，已保守跳过小红书采集",
                        confidence=0,
                        source="fallback",
                    )
                    for candidate in candidates
                ],
                status="fallback",
                prompt_slug=XHS_TARGET_SELECTION_PROMPT,
                error=error,
            )
        semaphore = asyncio.Semaphore(_AUTO_BATCH_CONCURRENCY)

        async def _select_chunk(
            chunk: list[XhsTargetCandidate],
        ) -> tuple[list[XhsTargetDecision], str]:
            async with semaphore:
                return await self._select_chunk_with_retry(
                    structured_llm,
                    prompt,
                    chunk,
                    project_id=project_id,
                    task_id=task_id,
                )

        chunks = [
            candidates[index : index + _AUTO_BATCH_SIZE]
            for index in range(0, len(candidates), _AUTO_BATCH_SIZE)
        ]
        chunk_results = await asyncio.gather(*[_select_chunk(chunk) for chunk in chunks])
        decisions = [decision for items, _error in chunk_results for decision in items]
        errors = [error for _items, error in chunk_results if error]
        return _build_result(
            mode="auto",
            decisions=decisions,
            status="fallback" if errors else "completed",
            prompt_slug=XHS_TARGET_SELECTION_PROMPT,
            error="; ".join(errors) or None,
        )

    async def _select_chunk_with_retry(
        self,
        structured_llm: Any,
        prompt: str,
        candidates: list[XhsTargetCandidate],
        *,
        project_id: str,
        task_id: str,
    ) -> tuple[list[XhsTargetDecision], str]:
        payload = [candidate.model_dump(mode="json") for candidate in candidates]
        expected_ids = [candidate.target_id for candidate in candidates]
        schema_json = json.dumps(
            _AiTargetDecisionBatch.model_json_schema(),
            ensure_ascii=False,
        )
        last_error = ""

        for attempt in range(2):
            retry_instruction = ""
            if attempt:
                retry_instruction = (
                    "\n\n前一次结构化输出无效，请重新完成同一判断。"
                    f"错误：{last_error}\n"
                    f"必须逐一返回这些 target_id：{json.dumps(expected_ids, ensure_ascii=False)}。\n"
                    "严格遵守以下 JSON Schema，不得遗漏、增添目标或输出解释文本：\n"
                    f"{schema_json}"
                )
            user_prompt = (
                "请判断以下目标是否应执行小红书采集。只能依据给出的目标信息，"
                "每个 target_id 必须且只能返回一次。\n\n"
                f"目标列表：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
                f"{retry_instruction}"
            )
            try:
                with observation_context(
                    project_id=project_id or None,
                    task_id=task_id or None,
                    phase=(
                        "xhs_target_selection"
                        if attempt == 0
                        else "xhs_target_selection_retry"
                    ),
                    agent="xhs_target_selector",
                    task_type="company_scan",
                ):
                    raw = await structured_llm.ainvoke(
                        [
                            SystemMessage(content=prompt),
                            HumanMessage(content=user_prompt),
                        ]
                    )
                batch = (
                    raw
                    if isinstance(raw, _AiTargetDecisionBatch)
                    else _AiTargetDecisionBatch.model_validate(raw)
                )
                by_id: dict[str, _AiTargetDecision] = {}
                for decision in batch.decisions:
                    if decision.target_id not in expected_ids:
                        raise ValueError(f"返回了未知 target_id: {decision.target_id}")
                    if decision.target_id in by_id:
                        raise ValueError(f"重复返回 target_id: {decision.target_id}")
                    by_id[decision.target_id] = decision
                missing_ids = [target_id for target_id in expected_ids if target_id not in by_id]
                if missing_ids:
                    raise ValueError(f"缺少 target_id: {', '.join(missing_ids)}")

                candidate_by_id = {item.target_id: item for item in candidates}
                return (
                    [
                        XhsTargetDecision(
                            target_id=target_id,
                            target_name=candidate_by_id[target_id].target_name,
                            target_category=by_id[target_id].target_category,
                            should_collect_xhs=by_id[target_id].should_collect_xhs,
                            reason=by_id[target_id].reason,
                            confidence=by_id[target_id].confidence,
                            source="ai",
                        )
                        for target_id in expected_ids
                    ],
                    "",
                )
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc) or type(exc).__name__
                logger.warning(
                    "XHS 目标结构化判定第 %s 次失败 task=%s targets=%s: %s",
                    attempt + 1,
                    task_id,
                    expected_ids,
                    last_error,
                )

        fallback = [
            XhsTargetDecision(
                target_id=candidate.target_id,
                target_name=candidate.target_name,
                target_category="unknown",
                should_collect_xhs=False,
                reason="AI 目标判定失败，已保守跳过小红书采集",
                confidence=0,
                source="fallback",
            )
            for candidate in candidates
        ]
        return fallback, last_error or "AI 目标判定失败"


class XhsTargetSelectionFactory:
    @staticmethod
    def create(
        mode: XhsTargetSelectionMode,
        *,
        app_config: Any,
        manual_targets: list[str],
    ) -> XhsTargetSelectionStrategy:
        if mode == "manual":
            return ManualXhsTargetSelectionStrategy(manual_targets)
        return AutomaticXhsTargetSelectionStrategy(app_config)


class XhsTargetSelectionService:
    """小红书目标选择统一入口。"""

    def __init__(
        self,
        app_config: Any,
        *,
        mode: Any = "auto",
        manual_targets: Any = None,
    ) -> None:
        self.mode = normalize_selection_mode(mode)
        self.manual_targets = parse_manual_targets(manual_targets)
        self.strategy = XhsTargetSelectionFactory.create(
            self.mode,
            app_config=app_config,
            manual_targets=self.manual_targets,
        )

    async def select(
        self,
        candidates: list[XhsTargetCandidate],
        *,
        project_id: str,
        task_id: str,
    ) -> XhsTargetSelectionResult:
        return await self.strategy.select(
            candidates,
            project_id=project_id,
            task_id=task_id,
        )


def merge_xhs_target_selection_results(
    *results: XhsTargetSelectionResult,
) -> XhsTargetSelectionResult:
    if not results:
        return _build_result(mode="auto", decisions=[])
    decisions_by_id: dict[str, XhsTargetDecision] = {}
    manual_targets: list[str] = []
    matched_manual_targets: list[str] = []
    errors: list[str] = []
    for result in results:
        decisions_by_id.update({item.target_id: item for item in result.decisions})
        manual_targets.extend(result.manual_targets)
        matched_manual_targets.extend(result.matched_manual_targets)
        if result.error:
            errors.append(result.error)
    first = results[0]
    return _build_result(
        mode=first.mode,
        decisions=list(decisions_by_id.values()),
        status="fallback" if any(item.status == "fallback" for item in results) else "completed",
        prompt_slug=first.prompt_slug,
        manual_targets=manual_targets,
        matched_manual_targets=matched_manual_targets,
        error="; ".join(dict.fromkeys(errors)) or None,
    )
