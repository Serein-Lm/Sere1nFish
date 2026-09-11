"""Structured requests carry their contract even when a gateway ignores it."""
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError

from Sere1nGraph.graph.agents.runtime import GuardedChatOpenAI


class ResearchPlan(BaseModel):
    strategy: str
    missions: list[str]


class BoundedPlan(BaseModel):
    persona_count: int = Field(ge=1, le=8)


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


@pytest.mark.parametrize("async_call", [False, True])
@pytest.mark.asyncio
async def test_invalid_count_is_corrected_once_with_field_feedback(monkeypatch, async_call):
    calls = []

    def generate(_self, messages, **kwargs):
        calls.append(messages)
        count = 12 if len(calls) == 1 else 6
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=json.dumps({"persona_count": count}),
            additional_kwargs={"parsed": {"persona_count": count}},
        ))])

    async def agenerate(*args, **kwargs):
        return generate(*args, **kwargs)

    monkeypatch.setattr(ChatOpenAI, "_generate", generate)
    monkeypatch.setattr(ChatOpenAI, "_agenerate", agenerate)
    model = GuardedChatOpenAI(model="test", api_key="test")
    structured = model.with_structured_output(BoundedPlan)
    result = await structured.ainvoke("计划") if async_call else structured.invoke("计划")
    assert result.persona_count == 6
    assert len(calls) == 2
    assert "persona_count" in calls[1][0].content
    assert "less than or equal to 8" in calls[1][0].content
    assert calls[0][-1].content == calls[1][-1].content == "计划"


def test_validation_retry_is_bounded_and_does_not_retry_transport_errors(monkeypatch):
    calls = 0
    failure = "validation"

    def generate(_self, messages, **kwargs):
        nonlocal calls
        calls += 1
        if failure == "transport":
            raise RuntimeError("connection unavailable")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content='{"persona_count":12}', additional_kwargs={"parsed": {"persona_count": 12}},
        ))])

    monkeypatch.setattr(ChatOpenAI, "_generate", generate)
    model = GuardedChatOpenAI(model="test", api_key="test")
    structured = model.with_structured_output(BoundedPlan)
    with pytest.raises(ValidationError):
        structured.invoke("计划")
    assert calls == 2
    calls, failure = 0, "transport"
    with pytest.raises(RuntimeError, match="connection unavailable"):
        structured.invoke("计划")
    assert calls == 1


@pytest.mark.asyncio
async def test_partial_streaming_remains_streamed_and_closes_on_cancel(monkeypatch):
    from langchain_core.runnables import RunnableGenerator

    closed = []

    async def stream(inputs):
        async for messages in inputs:
            assert '"required"' in messages[0].content
            try:
                yield {"persona_count": 1}
                yield {"persona_count": 6}
            finally:
                closed.append(True)

    monkeypatch.setattr(ChatOpenAI, "with_structured_output", lambda *a, **kw: RunnableGenerator(stream))
    model = GuardedChatOpenAI(model="test", api_key="test")
    stream = model.with_structured_output(BoundedPlan).astream("计划")
    assert await anext(stream) == {"persona_count": 1}
    await stream.aclose()
    assert closed == [True]
