"""Make the structured response contract visible across model gateways."""
from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import SystemMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError


_VALIDATION_ERRORS = (ValidationError, OutputParserException)


def _validation_feedback(error: Exception) -> str:
    """Return bounded field feedback without echoing model output or user data."""
    validation = error if isinstance(error, ValidationError) else error.__cause__
    issues = []
    if isinstance(validation, ValidationError):
        for item in validation.errors(include_input=False, include_url=False)[:12]:
            location = ".".join(str(part) for part in item.get("loc", ()))
            issues.append(f"{location}: {str(item.get('msg', ''))[:300]}")
    if not issues:
        issues.append("返回内容不是符合 Schema 的有效 JSON 数据实例")
    return (
        "\n上次返回未通过结构化校验，请根据以下反馈纠正并重新返回完整结果；"
        "仍需满足原请求的全部数量、类型和内容要求：\n" + "\n".join(issues)
    )


class SchemaBoundRunnable(Runnable):
    """Retry a complete invalid response once; preserve native partial streams."""

    def __init__(self, bound: Runnable, instructions: str, convert_input: Callable):
        self.bound = bound
        self.instructions = instructions
        self.convert_input = convert_input

    def _messages(self, model_input: Any, feedback: str = "") -> list:
        messages = self.convert_input(model_input).to_messages()
        return [SystemMessage(content=self.instructions + feedback), *messages]

    def invoke(self, input: Any, config=None, **kwargs: Any):
        feedback = ""
        for attempt in range(2):
            try:
                return self.bound.invoke(self._messages(input, feedback), config, **kwargs)
            except _VALIDATION_ERRORS as exc:
                if attempt:
                    raise
                feedback = _validation_feedback(exc)

    async def ainvoke(self, input: Any, config=None, **kwargs: Any):
        feedback = ""
        for attempt in range(2):
            try:
                return await self.bound.ainvoke(self._messages(input, feedback), config, **kwargs)
            except _VALIDATION_ERRORS as exc:
                if attempt:
                    raise
                feedback = _validation_feedback(exc)

    def stream(self, input: Any, config=None, **kwargs: Any):
        yield from self.bound.stream(self._messages(input), config, **kwargs)

    async def astream(self, input: Any, config=None, **kwargs: Any):
        stream = self.bound.astream(self._messages(input), config, **kwargs)
        try:
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()

    def get_input_schema(self, config=None):
        return self.bound.get_input_schema(config)

    def get_output_schema(self, config=None):
        return self.bound.get_output_schema(config)


def with_schema_instructions(
    runnable: Runnable,
    schema: Any,
    convert_input: Callable[[Any], PromptValue],
) -> Runnable:
    """Preserve native transport/validation and also send the schema as a message.

    Some compatible gateways accept ``response_format`` but fail to enforce it.
    Naming a Pydantic class in a prompt does not expose its fields to the model.
    This adapter keeps the provider parser authoritative while supplying the
    exact contract to the model, without rewriting the caller's messages.
    """
    if schema is None:
        return runnable
    parameters = convert_to_openai_tool(schema).get("function", {}).get("parameters")
    if parameters is None:
        return runnable
    instructions = (
        "结构化输出必须匹配下方 JSON Schema。严格遵守字段名、类型、层级、必填项和枚举。"
        "直接返回 Schema 描述的数据实例，不要额外包裹模型名、Schema 名或其他字段，"
        "不要返回 Schema 本身。使用工具提交结构化结果时，工具参数也必须遵循该结构。\n"
        "JSON Schema:\n"
        + json.dumps(parameters, ensure_ascii=False, separators=(",", ":"))
    )

    return SchemaBoundRunnable(runnable, instructions, convert_input)
