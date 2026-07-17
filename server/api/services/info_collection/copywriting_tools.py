"""Shared copywriting tool adapters for information collection flows."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from api.services.info_collection.contracts import CopywritingRequest, CopywritingResult
from core.logger import get_logger


logger = get_logger("info_collection.copywriting")


class AgentCopywritingTool:
    """Generate copywriting through the configured copywriting agent."""

    name = "agent_copywriting"

    def __init__(
        self,
        *,
        app_config: Any = None,
        agent: Any | None = None,
        response_parser: Callable[[Any], Any] | None = None,
        max_format_attempts: int = 2,
    ) -> None:
        self._app_config = app_config
        self._agent = agent
        self._response_parser = response_parser
        self._max_format_attempts = max(1, min(int(max_format_attempts or 1), 2))
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
        from api.utils.json_extract import extract_json_value

        messages = raw.get("messages", []) if isinstance(raw, dict) else []
        for msg in reversed(messages):
            for content in reversed(self._message_texts(msg)):
                try:
                    return extract_json_value(content)
                except Exception:
                    continue
        return None

    @staticmethod
    def _validate_response(request: CopywritingRequest, parsed: Any) -> list[dict[str, Any]]:
        items = parsed if isinstance(parsed, list) else [parsed]
        response_model = request.options.get("response_model")
        normalized: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("JSON 数组元素必须是对象")
            if response_model is None:
                normalized.append(dict(item))
                continue
            validated = response_model.model_validate(item)
            normalized.append(validated.model_dump(mode="json"))
        if not normalized:
            raise ValueError("JSON 结果为空")
        return normalized

    @staticmethod
    def _message_texts(message: Any) -> list[str]:
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return [content.strip()] if content.strip() else []
        if not isinstance(content, list):
            return []

        texts: list[str] = []
        for block in content:
            if isinstance(block, str) and block.strip():
                texts.append(block.strip())
                continue
            if not isinstance(block, dict):
                continue
            value = block.get("text") or block.get("content")
            if isinstance(value, str) and value.strip():
                texts.append(value.strip())
        return texts

    @classmethod
    def _last_response_text(cls, raw: Any) -> str:
        messages = raw.get("messages", []) if isinstance(raw, dict) else []
        for message in reversed(messages):
            texts = cls._message_texts(message)
            if texts:
                return "\n".join(texts)
        return ""

    @classmethod
    def _format_retry_prompt(
        cls,
        request: CopywritingRequest,
        raw: Any,
        parse_error: str,
        *,
        retry_number: int,
    ) -> str:
        previous = cls._last_response_text(raw)
        if len(previous) > 8000:
            previous = previous[-8000:]
        if not previous:
            previous = "（上一次响应没有可读取的文本内容）"
        return (
            f"{request.context}\n\n"
            f"# 输出格式纠正（第 {retry_number} 次）\n\n"
            "上一次响应没有通过结构化解析。请不要再次调用工具，直接根据原始任务和 "
            "JSON Schema 重新生成完整结果。\n"
            "只输出 JSON，不要输出解释、Markdown 代码块、前后缀或思考过程。"
            "顶层使用原任务要求的单个 JSON 对象或 JSON 数组，所有字符串必须正确转义。\n"
            f"解析失败原因: {(parse_error or '未找到有效 JSON 对象或数组')[:2000]}\n\n"
            "<previous_output>\n"
            f"{previous}\n"
            "</previous_output>"
        )

    async def generate(self, request: CopywritingRequest) -> CopywritingResult:
        from core.observability import observation_context
        from langchain_core.messages import HumanMessage

        try:
            agent = await self._get_agent()
        except Exception as exc:
            return CopywritingResult(
                source=request.source,
                project_id=request.project_id,
                task_id=request.task_id,
                target_id=request.target_id,
                meta={"error": str(exc)},
            )

        raw: Any = None
        normalized: list[dict[str, Any]] = []
        parse_error = ""
        attempts = 0
        for attempt_index in range(self._max_format_attempts):
            attempts = attempt_index + 1
            prompt = request.context
            if attempt_index:
                prompt = self._format_retry_prompt(
                    request,
                    raw,
                    parse_error,
                    retry_number=attempt_index,
                )
            try:
                with observation_context(
                    project_id=request.project_id,
                    task_id=request.task_id,
                    phase="copywriting",
                    agent="copywriting",
                    task_type=request.source,
                ):
                    raw = await agent({"messages": [HumanMessage(content=prompt)]})
            except Exception as exc:
                return CopywritingResult(
                    source=request.source,
                    project_id=request.project_id,
                    task_id=request.task_id,
                    target_id=request.target_id,
                    raw=raw,
                    meta={
                        "error": str(exc),
                        "attempts": attempts,
                        "format_retries": attempt_index,
                    },
                )

            try:
                parsed = self._parse_response(raw)
                if parsed is None:
                    raise ValueError("未找到有效 JSON 对象或数组")
                normalized = self._validate_response(request, parsed)
                parse_error = ""
            except Exception as exc:
                normalized = []
                parse_error = f"{type(exc).__name__}: {exc}"

            if normalized:
                break
            if attempts < self._max_format_attempts:
                logger.warning(
                    "话术输出格式无效，注入纠正提示后重试 "
                    "project=%s task=%s target=%s attempt=%s/%s error=%s",
                    request.project_id,
                    request.task_id,
                    request.target_id,
                    attempts,
                    self._max_format_attempts,
                    parse_error,
                )

        if not normalized:
            return CopywritingResult(
                source=request.source,
                project_id=request.project_id,
                task_id=request.task_id,
                target_id=request.target_id,
                raw=raw,
                meta={
                    "error": f"Agent 输出解析失败: {parse_error}",
                    "attempts": attempts,
                    "format_retries": max(0, attempts - 1),
                },
            )

        copywritings: list[dict[str, Any]] = []
        for item in normalized:
            doc = dict(item)
            doc["status"] = "completed"
            copywritings.append(doc)

        return CopywritingResult(
            source=request.source,
            project_id=request.project_id,
            task_id=request.task_id,
            target_id=request.target_id,
            copywritings=copywritings,
            raw=raw,
            meta={
                "count": len(copywritings),
                "attempts": attempts,
                "format_retries": max(0, attempts - 1),
            },
        )
