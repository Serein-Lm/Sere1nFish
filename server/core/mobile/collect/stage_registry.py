"""Declarative registry for the mobile item pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.stream import Pipeline, Stage


StageFactory = Callable[[], Stage]


@dataclass(frozen=True, slots=True)
class MobileStageDefinition:
    name: str
    factory: StageFactory
    downstream: tuple[str, ...] = ()


class MobileStageRegistry:
    """Validate and build the collection DAG from registered stage adapters."""

    def __init__(self) -> None:
        self._definitions: dict[str, MobileStageDefinition] = {}

    def register(self, definition: MobileStageDefinition) -> "MobileStageRegistry":
        if not definition.name:
            raise ValueError("手机采集 Stage 名称不能为空")
        if definition.name in self._definitions:
            raise ValueError(f"手机采集 Stage 重复注册: {definition.name}")
        self._definitions[definition.name] = definition
        return self

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def build(
        self,
        *,
        state: dict[str, Any],
        pipeline_id: str,
        entry: str,
    ) -> Pipeline:
        self._validate(entry)
        pipeline = Pipeline(state=state, pipeline_id=pipeline_id)
        for definition in self._definitions.values():
            stage = definition.factory()
            if stage.name != definition.name:
                raise ValueError(
                    f"手机采集 Stage 注册名不一致: {definition.name} != {stage.name}"
                )
            pipeline.add(stage, downstream=list(definition.downstream))
        return pipeline

    def _validate(self, entry: str) -> None:
        if entry not in self._definitions:
            raise ValueError(f"手机采集入口 Stage 未注册: {entry}")
        names = set(self._definitions)
        for definition in self._definitions.values():
            missing = set(definition.downstream).difference(names)
            if missing:
                raise ValueError(
                    f"手机采集 Stage {definition.name} 下游未注册: "
                    + ", ".join(sorted(missing))
                )

    @classmethod
    def default(
        cls,
        *,
        collect: type[Stage],
        persist: type[Stage],
        notify: type[Stage],
    ) -> "MobileStageRegistry":
        return (
            cls()
            .register(MobileStageDefinition("collect", collect, ("persist",)))
            .register(MobileStageDefinition("persist", persist, ("notify",)))
            .register(MobileStageDefinition("notify", notify))
        )
