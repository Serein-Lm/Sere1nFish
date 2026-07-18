"""Usage-aware adapter for the vendored AutoGLM OpenAI client.

AutoGLM uses the native ``openai.AsyncOpenAI`` client, so LangChain callbacks
cannot observe its requests. This adapter keeps AutoGLM unchanged and records
the usage object returned by chat completions through the shared observability
service.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from core.observability import observation_context, record_llm_usage


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def extract_usage(usage: Any) -> tuple[int, int]:
    """Extract prompt/completion counts from dict or OpenAI model objects."""
    input_tokens = _field(usage, "prompt_tokens")
    if input_tokens is None:
        input_tokens = _field(usage, "input_tokens", 0)
    output_tokens = _field(usage, "completion_tokens")
    if output_tokens is None:
        output_tokens = _field(usage, "output_tokens", 0)
    try:
        return max(0, int(input_tokens or 0)), max(0, int(output_tokens or 0))
    except (TypeError, ValueError):
        return 0, 0


class _TrackedStream:
    def __init__(
        self,
        stream: Any,
        *,
        model: str,
        project_id: str,
        task_id: str,
        run_id: str,
        started_at: float,
    ) -> None:
        self._stream = stream
        self._iterator = stream.__aiter__()
        self._model = model
        self._project_id = project_id
        self._task_id = task_id
        self._run_id = run_id
        self._started_at = started_at
        self._recorded = False

    def __aiter__(self) -> "_TrackedStream":
        return self

    async def __anext__(self) -> Any:
        try:
            chunk = await self._iterator.__anext__()
        except StopAsyncIteration:
            self._record_usage(None)
            raise
        usage = _field(chunk, "usage")
        if usage is not None:
            self._record_usage(usage)
        return chunk

    def _record_usage(self, usage: Any) -> None:
        if self._recorded:
            return
        input_tokens, output_tokens = extract_usage(usage)
        if input_tokens == 0 and output_tokens == 0:
            return
        self._recorded = True
        with observation_context(
            project_id=self._project_id,
            task_id=self._task_id,
            turn_id=self._run_id,
            phase="mobile_executor",
            agent="mobile_executor",
            task_type="mobile",
        ):
            record_llm_usage(
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=(time.perf_counter() - self._started_at) * 1000,
                run_id=self._run_id,
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class _CompletionProxy:
    def __init__(
        self,
        completions: Any,
        *,
        model: str,
        project_id: str,
        task_id: str,
    ) -> None:
        self._completions = completions
        self._model = model
        self._project_id = project_id
        self._task_id = task_id

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        request = dict(kwargs)
        stream = bool(request.get("stream"))
        if stream:
            stream_options = dict(request.get("stream_options") or {})
            stream_options.setdefault("include_usage", True)
            request["stream_options"] = stream_options

        run_id = uuid.uuid4().hex
        started_at = time.perf_counter()
        with observation_context(
            project_id=self._project_id,
            task_id=self._task_id,
            turn_id=run_id,
            phase="mobile_executor",
            agent="mobile_executor",
            task_type="mobile",
        ):
            response = await self._completions.create(*args, **request)
            if not stream:
                input_tokens, output_tokens = extract_usage(
                    _field(response, "usage")
                )
                record_llm_usage(
                    model=str(request.get("model") or self._model),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                    run_id=run_id,
                )
                return response

        return _TrackedStream(
            response,
            model=str(request.get("model") or self._model),
            project_id=self._project_id,
            task_id=self._task_id,
            run_id=run_id,
            started_at=started_at,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._completions, name)


class _ChatProxy:
    def __init__(self, chat: Any, completions: _CompletionProxy) -> None:
        self._chat = chat
        self.completions = completions

    def __getattr__(self, name: str) -> Any:
        return getattr(self._chat, name)


class _ClientProxy:
    def __init__(self, client: Any, chat: _ChatProxy) -> None:
        self._client = client
        self.chat = chat

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def instrument_agent(
    agent: Any,
    *,
    model: str,
    project_id: str = "",
    task_id: str = "",
) -> Any:
    """Wrap an AutoGLM agent's chat client; leave other agent types untouched."""
    client = getattr(agent, "openai_client", None)
    chat = getattr(client, "chat", None)
    completions = getattr(chat, "completions", None)
    if client is None or chat is None or completions is None:
        return agent
    if isinstance(completions, _CompletionProxy):
        return agent
    proxy = _CompletionProxy(
        completions,
        model=str(model or "unknown"),
        project_id=str(project_id or ""),
        task_id=str(task_id or ""),
    )
    agent.openai_client = _ClientProxy(client, _ChatProxy(chat, proxy))
    return agent


__all__ = ["extract_usage", "instrument_agent"]
