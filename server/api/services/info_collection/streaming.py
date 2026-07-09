"""Shared helpers for information-collection stream orchestration."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.stream import Item, Pipeline, Stage
from core.stream.dlq import DeadLetter


MetaBuilder = Callable[[Any, int, int], dict[str, Any]]


@dataclass(frozen=True)
class StreamStageSpec:
    """Registration data for one Stage in an information collection stream."""

    stage: Stage
    downstream: list[str] = field(default_factory=list)


def stream_stage(stage: Stage, *, downstream: Sequence[str] | None = None) -> StreamStageSpec:
    return StreamStageSpec(stage=stage, downstream=list(downstream or []))


def make_stream_items(
    payloads: Iterable[Any],
    *,
    indexed: bool = False,
    meta_builder: MetaBuilder | None = None,
) -> list[Item]:
    """Create stream Items with optional stable idx/total metadata."""

    payload_list = list(payloads)
    total = len(payload_list)
    items: list[Item] = []
    for idx, payload in enumerate(payload_list):
        if isinstance(payload, Item):
            item = payload
        else:
            item = Item(payload=payload)
        meta = meta_builder(item.payload, idx, total) if meta_builder else {}
        if indexed:
            meta = {"idx": idx, "total": total, **meta}
        if meta:
            item.meta.update(meta)
        items.append(item)
    return items


async def run_stream_pipeline(
    *,
    stages: Sequence[StreamStageSpec],
    seeds: Iterable[Item | Any],
    entry: str,
    state: dict[str, Any] | None = None,
    dlq: DeadLetter | None = None,
    pipeline_id: str = "",
    worker_get_timeout: float = 0.5,
    on_pipeline_ready: Callable[[Pipeline], Any] | None = None,
) -> Pipeline:
    """Build and run a core.stream Pipeline, returning it for state/metrics access."""

    pipeline_state = state if state is not None else {}
    pipe = Pipeline(
        state=pipeline_state,
        dlq=dlq,
        pipeline_id=pipeline_id,
        worker_get_timeout=worker_get_timeout,
    )
    # core.stream treats an empty dict as falsy in __init__; keep the caller's
    # state object stable so orchestration wrappers can read/write it directly.
    pipe.state = pipeline_state
    for spec in stages:
        pipe.add(spec.stage, downstream=spec.downstream)
    if on_pipeline_ready:
        ready_result = on_pipeline_ready(pipe)
        if inspect.isawaitable(ready_result):
            await ready_result
    await pipe.run(seeds=seeds, entry=entry)
    return pipe
