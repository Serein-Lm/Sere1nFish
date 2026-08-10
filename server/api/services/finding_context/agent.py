"""Finding 上下文 Agent 协议、运行时适配器与工厂。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from Sere1nGraph.graph.agents.runtime import create_llm
from Sere1nGraph.graph.prompts.loader import load_prompt
from api.services.runtime_config import get_runtime_app_config
from core.observability import observation_context

from .schemas import FindingContextResult


PROMPT_SLUG = "finding_context/organizer"
_AGENT_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class FindingContextImage:
    evidence_ref: str
    data_url: str


@dataclass(frozen=True)
class FindingContextAgentRequest:
    finding_id: str
    project_id: str
    task_id: str
    text: str
    images: list[FindingContextImage]


@dataclass(frozen=True)
class FindingContextAgentResponse:
    result: FindingContextResult
    model: str


class FindingContextAgent(Protocol):
    async def organize(
        self,
        request: FindingContextAgentRequest,
    ) -> FindingContextAgentResponse: ...


def parse_finding_context_result(value: Any) -> FindingContextResult:
    """先统一模型供应商的外层包装，再校验领域 Schema。"""
    if isinstance(value, FindingContextResult):
        return value
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise ValueError("Finding 上下文模型必须返回唯一对象")
        value = value[0]
    return FindingContextResult.model_validate(value)


class RuntimeFindingContextAgent:
    """通过统一模型运行时执行一次受 Schema 约束的多模态整理。"""

    async def organize(
        self,
        request: FindingContextAgentRequest,
    ) -> FindingContextAgentResponse:
        app_config = await get_runtime_app_config()
        model_name = (
            app_config.runtime.models.vision
            if request.images
            else app_config.runtime.models.default
        )
        llm = create_llm(app_config, model_name=model_name, streaming=False)
        # 使用 JSON Schema 让供应商适配器先返回原始结构；领域层统一处理
        # 部分兼容接口偶发的单元素数组包装。
        structured = llm.with_structured_output(
            FindingContextResult.model_json_schema()
        )
        content: list[dict[str, object]] = [
            {"type": "text", "text": request.text}
        ]
        for image in request.images:
            content.extend(
                [
                    {
                        "type": "text",
                        "text": f"下面是视觉证据 {image.evidence_ref}：",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": image.data_url},
                    },
                ]
            )
        with observation_context(
            project_id=request.project_id or None,
            task_id=request.task_id or None,
            phase="finding_context_organization",
            agent="finding_context_organizer",
            task_type="finding_context",
        ):
            result = await asyncio.wait_for(
                structured.ainvoke(
                    [
                        SystemMessage(content=load_prompt(PROMPT_SLUG)),
                        HumanMessage(content=content),
                    ]
                ),
                timeout=_AGENT_TIMEOUT_SECONDS,
            )
        parsed = parse_finding_context_result(result)
        return FindingContextAgentResponse(result=parsed, model=model_name)


def create_finding_context_agent() -> FindingContextAgent:
    return RuntimeFindingContextAgent()
