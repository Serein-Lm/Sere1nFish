"""Make the structured response contract visible across model gateways."""
from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.messages import SystemMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool


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

    def prepare(model_input: Any) -> list:
        messages = convert_input(model_input).to_messages()
        return [SystemMessage(content=instructions), *messages]

    return RunnableLambda(prepare, name="structured_output_contract") | runnable
