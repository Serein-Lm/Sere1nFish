"""Shared copywriting tool adapters for information collection flows."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from api.services.info_collection.contracts import CopywritingRequest, CopywritingResult


class AgentCopywritingTool:
    """Generate copywriting through the configured copywriting agent."""

    name = "agent_copywriting"

    def __init__(
        self,
        *,
        app_config: Any = None,
        agent: Any | None = None,
        response_parser: Callable[[Any], Any] | None = None,
    ) -> None:
        self._app_config = app_config
        self._agent = agent
        self._response_parser = response_parser
        self._agent_lock = asyncio.Lock()

    async def _get_agent(self) -> Any:
        if self._agent is not None:
            return self._agent
        async with self._agent_lock:
            if self._agent is None:
                from Sere1nGraph.graph.agents.factory import create_copywriting_agent

                self._agent = await create_copywriting_agent(self._app_config)
        return self._agent

    def _parse_response(self, raw: Any) -> Any:
        if self._response_parser:
            return self._response_parser(raw)
        from api.utils.json_extract import extract_json_object

        messages = raw.get("messages", []) if isinstance(raw, dict) else []
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip():
                try:
                    return extract_json_object(content.strip())
                except Exception:
                    continue
        return None

    async def generate(self, request: CopywritingRequest) -> CopywritingResult:
        from core.observability import observation_context
        from langchain_core.messages import HumanMessage

        try:
            agent = await self._get_agent()
            with observation_context(
                project_id=request.project_id,
                task_id=request.task_id,
                phase="copywriting",
                agent="copywriting",
            ):
                raw = await agent({"messages": [HumanMessage(content=request.context)]})
            parsed = self._parse_response(raw)
        except Exception as exc:
            return CopywritingResult(
                source=request.source,
                project_id=request.project_id,
                task_id=request.task_id,
                target_id=request.target_id,
                meta={"error": str(exc)},
            )

        if not parsed:
            return CopywritingResult(
                source=request.source,
                project_id=request.project_id,
                task_id=request.task_id,
                target_id=request.target_id,
                raw=raw,
                meta={"error": "Agent 输出解析失败"},
            )

        copywritings = parsed if isinstance(parsed, list) else [parsed]
        normalized: list[dict[str, Any]] = []
        for item in copywritings:
            if not isinstance(item, dict):
                continue
            doc = dict(item)
            doc.setdefault("status", "completed")
            normalized.append(doc)

        if not normalized:
            return CopywritingResult(
                source=request.source,
                project_id=request.project_id,
                task_id=request.task_id,
                target_id=request.target_id,
                raw=raw,
                meta={"error": "Agent 输出不是有效话术对象"},
            )

        return CopywritingResult(
            source=request.source,
            project_id=request.project_id,
            task_id=request.task_id,
            target_id=request.target_id,
            copywritings=normalized,
            raw=raw,
            meta={"count": len(normalized)},
        )
