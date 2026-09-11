"""Structured requests carry their contract even when a gateway ignores it."""
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from Sere1nGraph.graph.agents.runtime import GuardedChatOpenAI


class ResearchPlan(BaseModel):
    strategy: str
    missions: list[str]


@pytest.mark.parametrize("input_kind", ["text", "messages", "prompt"])
def test_schema_reaches_provider_and_original_input_is_preserved(monkeypatch, input_kind):
    original = [SystemMessage(content="研究规则"), HumanMessage(content="制定研究计划")]
    inputs = {
        "text": "制定研究计划",
        "messages": original,
        "prompt": ChatPromptTemplate.from_messages(original).invoke({}),
    }
    captured = []

    def generate(_self, messages, **kwargs):
        captured.extend(messages)
        assert kwargs["response_format"] is ResearchPlan
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content='{"strategy":"分工","missions":["行业"]}',
            additional_kwargs={"parsed": {"strategy": "分工", "missions": ["行业"]}},
        ))])

    monkeypatch.setattr(ChatOpenAI, "_generate", generate)
    model = GuardedChatOpenAI(model="test", api_key="test")
    result = model.with_structured_output(ResearchPlan).invoke(inputs[input_kind])

    assert result == ResearchPlan(strategy="分工", missions=["行业"])
    schema_messages = [m for m in captured if isinstance(m, SystemMessage) and '"required"' in m.content]
    assert len(schema_messages) == 1
    assert '"strategy"' in schema_messages[0].content
    assert '"missions"' in schema_messages[0].content
    assert len(original) == 2
    assert captured[-1].content == "制定研究计划"


@pytest.mark.asyncio
async def test_async_raw_parse_error_is_preserved_and_capacity_released(monkeypatch):
    from Sere1nGraph.graph.agents import runtime
    from core.llm_capacity import LLMCapacityGuard

    guard = LLMCapacityGuard(max_concurrency=1)

    async def generate(_self, messages, **kwargs):
        assert guard.status()["in_use"] == 1
        assert any('"missions"' in m.content for m in messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content='{"wrong_wrapper": {}}', additional_kwargs={"parsed": {"wrong_wrapper": {}}},
        ))])

    monkeypatch.setattr(runtime, "get_global_llm_capacity_guard", lambda: guard)
    monkeypatch.setattr(ChatOpenAI, "_agenerate", generate)
    model = GuardedChatOpenAI(model="test", api_key="test")
    result = await model.with_structured_output(ResearchPlan, include_raw=True).ainvoke("计划")

    assert result["parsed"] is None
    assert isinstance(result["parsing_error"], ValidationError)
    assert isinstance(result["raw"], AIMessage)
    assert guard.status()["in_use"] == 0


@pytest.mark.parametrize("method", ["json_mode", "function_calling"])
def test_existing_transport_and_parser_options_are_preserved(monkeypatch, method):
    def generate(_self, messages, **kwargs):
        assert any('"missions"' in m.content for m in messages)
        payload = {"strategy": "分工", "missions": ["行业"]}
        if method == "function_calling":
            assert kwargs["tools"][0]["function"]["name"] == "ResearchPlan"
            message = AIMessage(content="", tool_calls=[{
                "name": "ResearchPlan", "args": payload, "id": "call-plan", "type": "tool_call",
            }])
        else:
            assert kwargs["response_format"] == {"type": "json_object"}
            message = AIMessage(content=json.dumps(payload))
        return ChatResult(generations=[ChatGeneration(message=message)])

    monkeypatch.setattr(ChatOpenAI, "_generate", generate)
    model = GuardedChatOpenAI(model="test", api_key="test")
    result = model.with_structured_output(ResearchPlan, method=method).invoke("计划")
    assert result.strategy == "分工"


def test_json_schema_and_tool_schema_are_unwrapped_and_config_survives(monkeypatch):
    seen = []

    def structured(_self, schema, **kwargs):
        assert kwargs == {"method": "function_calling", "strict": True}
        def capture(messages, config):
            seen.append((messages, config))
            return {"strategy": "分工", "missions": ["行业"]}
        return RunnableLambda(capture)

    monkeypatch.setattr(ChatOpenAI, "with_structured_output", structured)
    model = GuardedChatOpenAI(model="test", api_key="test")
    schema = {"type": "function", "function": {
        "name": "plan", "parameters": ResearchPlan.model_json_schema(),
    }}
    model.with_structured_output(schema, method="function_calling", strict=True).invoke(
        "计划", config={"metadata": {"task_id": "task-plan"}},
    )
    assert '"required"' in seen[0][0][0].content
    assert seen[0][1]["metadata"]["task_id"] == "task-plan"


def test_schemaless_json_mode_keeps_existing_input(monkeypatch):
    monkeypatch.setattr(ChatOpenAI, "with_structured_output", lambda *a, **kw: RunnableLambda(lambda x: x))
    model = GuardedChatOpenAI(model="test", api_key="test")
    assert model.with_structured_output(method="json_mode").invoke("json please") == "json please"
