"""微信公众号采集目标选择策略。

公众号手机资源优先用于成熟、机构型目标。自动模式通过数据库 Prompt 和统一
LLM 运行时判断；全部模式用于用户明确要求跳过筛选的场景。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Literal, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from Sere1nGraph.graph.agents.runtime import create_llm
from Sere1nGraph.graph.prompts.loader import load_prompt
from core.logger import get_logger
from core.observability import observation_context


logger = get_logger("wechat_target_selection")

WECHAT_TARGET_SELECTION_PROMPT = (
    "wechat_target_selection/wechat_target_selection"
)
WechatTargetSelectionMode = Literal["auto", "all"]
WechatAutoTargetCategory = Literal[
    "government_public_institution",
    "traditional_state_owned_enterprise",
    "exchange_financial_infrastructure",
    "broadcast_news_media",
    "education_research_healthcare",
    "mature_financial_institution",
    "traditional_large_enterprise",
    "internet_consumer_brand",
    "new_or_lightweight_company",
    "other",
    "unknown",
]
WechatTargetCategory = WechatAutoTargetCategory | Literal["all"]

_AUTO_BATCH_SIZE = 25
_AUTO_BATCH_CONCURRENCY = 3
_PROHIBITED_EXISTENCE_REASON_MARKERS = (
    "疑似虚构",
    "名称错误",
    "可能不存在",
    "名称混淆",
    "无法确认真实",
    "无法确认为真实",
)
_INSTITUTION_NAME_MARKERS = (
    "广播电视台",
    "广播电视总台",
    "电视台",
    "人民日报社",
    "通讯社",
    "交易所",
    "清算中心",
    "清算服务",
    "中国银联",
    "人民银行",
    "外汇管理局",
    "教育部",
    "管理委员会",
    "管理中心",
    "管理局",
    "大学",
    "科学院",
    "研究院",
    "医院",
)
_TRADITIONAL_LARGE_INDUSTRIES = {
    "airport",
    "finance",
    "manufacturing",
    "energy",
    "logistics",
    "telecom",
    "media",
}


def _explicit_institution_category(
    candidate: "WechatTargetCandidate",
) -> WechatAutoTargetCategory | None:
    names = " ".join([candidate.target_name, *candidate.aliases])
    if "交易所" in names or any(
        marker in names
        for marker in ("清算中心", "清算服务", "中国银联", "人民银行")
    ):
        return "exchange_financial_infrastructure"
    if any(
        marker in names
        for marker in ("广播电视台", "广播电视总台", "电视台", "人民日报社", "通讯社")
    ):
        return "broadcast_news_media"
    if any(marker in names for marker in ("大学", "科学院", "研究院", "医院")):
        return "education_research_healthcare"
    if any(
        marker in names
        for marker in ("教育部", "管理委员会", "管理中心", "管理局", "外汇管理局")
    ):
        return "government_public_institution"
    return None


class WechatTargetCandidate(BaseModel):
    target_id: str = Field(min_length=1)
    target_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    root_domain: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class _AiTargetDecision(BaseModel):
    target_id: str = Field(min_length=1)
    target_category: WechatAutoTargetCategory
    should_collect_wechat: bool
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
        if any(
            marker in text
            for marker in _PROHIBITED_EXISTENCE_REASON_MARKERS
        ):
            raise ValueError("reason 禁止以目标名称真实性作为采集判断依据")
        return text


class _AiTargetDecisionBatch(BaseModel):
    decisions: list[_AiTargetDecision]


class WechatTargetDecision(BaseModel):
    target_id: str
    target_name: str
    target_category: WechatTargetCategory
    should_collect_wechat: bool
    reason: str
    confidence: float = Field(ge=0, le=1)
    source: Literal["ai", "all", "fallback"]


class WechatTargetSelectionResult(BaseModel):
    mode: WechatTargetSelectionMode
    status: Literal["completed", "fallback"] = "completed"
    prompt_slug: str | None = None
    decisions: list[WechatTargetDecision] = Field(default_factory=list)
    selected_count: int = 0
    skipped_count: int = 0
    error: str | None = None


class WechatTargetSelectionStrategy(Protocol):
    async def select(
        self,
        candidates: list[WechatTargetCandidate],
        *,
        project_id: str,
        task_id: str,
    ) -> WechatTargetSelectionResult: ...


def normalize_wechat_selection_mode(value: Any) -> WechatTargetSelectionMode:
    mode = str(value or "auto").strip().lower()
    if mode not in {"auto", "all"}:
        raise ValueError("wechat_target_selection_mode 只支持 auto 或 all")
    return mode  # type: ignore[return-value]


def _build_result(
    *,
    mode: WechatTargetSelectionMode,
    decisions: list[WechatTargetDecision],
    status: Literal["completed", "fallback"] = "completed",
    prompt_slug: str | None = None,
    error: str | None = None,
) -> WechatTargetSelectionResult:
    return WechatTargetSelectionResult(
        mode=mode,
        status=status,
        prompt_slug=prompt_slug,
        decisions=decisions,
        selected_count=sum(
            1 for item in decisions if item.should_collect_wechat
        ),
        skipped_count=sum(
            1 for item in decisions if not item.should_collect_wechat
        ),
        error=error,
    )


def _fallback_decision(
    candidate: WechatTargetCandidate,
) -> WechatTargetDecision:
    """Keep obvious mature institutions running when the model is unavailable."""
    context = candidate.context
    industry = str(context.get("industry") or "").strip().lower()
    scale = str(context.get("scale") or "").strip().lower()
    names = " ".join([candidate.target_name, *candidate.aliases])

    explicit_category = _explicit_institution_category(candidate)
    if explicit_category:
        category: WechatTargetCategory = explicit_category
        selected = True
        reason = "名称明确体现成熟公共机构或传统单位属性"
    elif any(marker in names for marker in _INSTITUTION_NAME_MARKERS):
        category = "government_public_institution"
        selected = True
        reason = "名称明确体现成熟公共机构属性"
    elif industry in {"government", "education", "healthcare"}:
        category = (
            "government_public_institution"
            if industry == "government"
            else "education_research_healthcare"
        )
        selected = True
        reason = "公司路由画像表明其属于公共服务型成熟机构"
    elif scale == "large" and industry in _TRADITIONAL_LARGE_INDUSTRIES:
        category = (
            "mature_financial_institution"
            if industry == "finance"
            else "traditional_large_enterprise"
        )
        selected = True
        reason = "公司路由画像表明其为大型传统行业单位"
    else:
        category = "unknown"
        selected = False
        reason = "现有画像不足以确认其属于成熟机构型公众号目标"

    return WechatTargetDecision(
        target_id=candidate.target_id,
        target_name=candidate.target_name,
        target_category=category,
        should_collect_wechat=selected,
        reason=reason,
        confidence=0.55 if selected else 0.25,
        source="fallback",
    )


class AllWechatTargetSelectionStrategy:
    async def select(
        self,
        candidates: list[WechatTargetCandidate],
        *,
        project_id: str,
        task_id: str,
    ) -> WechatTargetSelectionResult:
        del project_id, task_id
        return _build_result(
            mode="all",
            decisions=[
                WechatTargetDecision(
                    target_id=candidate.target_id,
                    target_name=candidate.target_name,
                    target_category="all",
                    should_collect_wechat=True,
                    reason="用户明确选择公众号全部目标模式",
                    confidence=1,
                    source="all",
                )
                for candidate in candidates
            ],
        )


class AutomaticWechatTargetSelectionStrategy:
    def __init__(self, app_config: Any) -> None:
        self.app_config = app_config

    async def select(
        self,
        candidates: list[WechatTargetCandidate],
        *,
        project_id: str,
        task_id: str,
    ) -> WechatTargetSelectionResult:
        if not candidates:
            return _build_result(
                mode="auto",
                decisions=[],
                prompt_slug=WECHAT_TARGET_SELECTION_PROMPT,
            )

        try:
            prompt = load_prompt(WECHAT_TARGET_SELECTION_PROMPT)
            llm = create_llm(self.app_config, streaming=False)
            structured_llm = llm.with_structured_output(
                _AiTargetDecisionBatch
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc) or type(exc).__name__
            logger.warning(
                "公众号目标判定运行时不可用 task=%s: %s",
                task_id,
                error,
            )
            return _build_result(
                mode="auto",
                decisions=[_fallback_decision(item) for item in candidates],
                status="fallback",
                prompt_slug=WECHAT_TARGET_SELECTION_PROMPT,
                error=error,
            )

        semaphore = asyncio.Semaphore(_AUTO_BATCH_CONCURRENCY)

        async def _select_chunk(
            chunk: list[WechatTargetCandidate],
        ) -> tuple[list[WechatTargetDecision], str]:
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
        chunk_results = await asyncio.gather(
            *[_select_chunk(chunk) for chunk in chunks]
        )
        decisions = [
            decision
            for items, _error in chunk_results
            for decision in items
        ]
        errors = [error for _items, error in chunk_results if error]
        return _build_result(
            mode="auto",
            decisions=decisions,
            status="fallback" if errors else "completed",
            prompt_slug=WECHAT_TARGET_SELECTION_PROMPT,
            error="; ".join(errors) or None,
        )

    async def _select_chunk_with_retry(
        self,
        structured_llm: Any,
        prompt: str,
        candidates: list[WechatTargetCandidate],
        *,
        project_id: str,
        task_id: str,
    ) -> tuple[list[WechatTargetDecision], str]:
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
                    "必须逐一返回这些 target_id："
                    f"{json.dumps(expected_ids, ensure_ascii=False)}。\n"
                    "严格遵守以下 JSON Schema，不得遗漏、增添目标或输出解释文本：\n"
                    f"{schema_json}"
                )
            user_prompt = (
                "请判断以下目标是否值得占用手机执行微信公众号采集。"
                "每个 target_id 必须且只能返回一次。\n\n"
                f"目标列表：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
                f"{retry_instruction}"
            )
            try:
                with observation_context(
                    project_id=project_id or None,
                    task_id=task_id or None,
                    phase=(
                        "wechat_target_selection"
                        if attempt == 0
                        else "wechat_target_selection_retry"
                    ),
                    agent="wechat_target_selector",
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
                        raise ValueError(
                            f"返回了未知 target_id: {decision.target_id}"
                        )
                    if decision.target_id in by_id:
                        raise ValueError(
                            f"重复返回 target_id: {decision.target_id}"
                        )
                    by_id[decision.target_id] = decision
                missing = [
                    target_id
                    for target_id in expected_ids
                    if target_id not in by_id
                ]
                if missing:
                    raise ValueError(f"缺少 target_id: {', '.join(missing)}")

                candidate_by_id = {
                    item.target_id: item for item in candidates
                }
                explicit_categories = {
                    target_id: _explicit_institution_category(
                        candidate_by_id[target_id]
                    )
                    for target_id in expected_ids
                }
                normalized_categories = {
                    target_id: (
                        explicit_categories[target_id]
                        or by_id[target_id].target_category
                    )
                    for target_id in expected_ids
                }
                selected_by_policy = {
                    target_id: bool(explicit_categories[target_id])
                    or by_id[target_id].should_collect_wechat
                    for target_id in expected_ids
                }
                return (
                    [
                        WechatTargetDecision(
                            target_id=target_id,
                            target_name=candidate_by_id[target_id].target_name,
                            target_category=normalized_categories[target_id],
                            should_collect_wechat=selected_by_policy[target_id],
                            reason=(
                                by_id[target_id].reason
                                if by_id[target_id].should_collect_wechat
                                or not explicit_categories[target_id]
                                else "名称明确命中成熟机构公众号采集规则"
                            ),
                            confidence=(
                                max(by_id[target_id].confidence, 0.9)
                                if explicit_categories[target_id]
                                else by_id[target_id].confidence
                            ),
                            source="ai",
                        )
                        for target_id in expected_ids
                    ],
                    "",
                )
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc) or type(exc).__name__
                logger.warning(
                    "公众号目标结构化判定第 %s 次失败 task=%s targets=%s: %s",
                    attempt + 1,
                    task_id,
                    expected_ids,
                    last_error,
                )

        return (
            [_fallback_decision(item) for item in candidates],
            last_error or "公众号目标判定失败",
        )


class WechatTargetSelectionFactory:
    @staticmethod
    def create(
        mode: WechatTargetSelectionMode,
        *,
        app_config: Any,
    ) -> WechatTargetSelectionStrategy:
        if mode == "all":
            return AllWechatTargetSelectionStrategy()
        return AutomaticWechatTargetSelectionStrategy(app_config)


class WechatTargetSelectionService:
    """公众号目标选择统一入口。"""

    def __init__(self, app_config: Any, *, mode: Any = "auto") -> None:
        self.mode = normalize_wechat_selection_mode(mode)
        self.strategy = WechatTargetSelectionFactory.create(
            self.mode,
            app_config=app_config,
        )

    async def select(
        self,
        candidates: list[WechatTargetCandidate],
        *,
        project_id: str,
        task_id: str,
    ) -> WechatTargetSelectionResult:
        return await self.strategy.select(
            candidates,
            project_id=project_id,
            task_id=task_id,
        )
