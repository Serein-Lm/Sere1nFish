"""Registry-backed mobile collection scoring policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.mobile.collect.contacts import grade_with_contacts


class ScorePolicy(Protocol):
    name: str

    def score(self, raw_score: object, *, has_contacts: bool) -> int | None: ...


def _raw_score(value: object) -> int | None:
    if value is None:
        return None
    try:
        return max(0, min(int(value), 100))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True, slots=True)
class ContactWeightedScorePolicy:
    name: str = "contact_weighted"

    def score(self, raw_score: object, *, has_contacts: bool) -> int | None:
        return grade_with_contacts(_raw_score(raw_score), has_contacts)


@dataclass(frozen=True, slots=True)
class RawScorePolicy:
    name: str = "raw"

    def score(self, raw_score: object, *, has_contacts: bool) -> int | None:
        del has_contacts
        return _raw_score(raw_score)


class ScorePolicyRegistry:
    _policies: dict[str, ScorePolicy] = {
        "contact_weighted": ContactWeightedScorePolicy(),
        "raw": RawScorePolicy(),
    }

    @classmethod
    def register(cls, policy: ScorePolicy) -> None:
        name = str(policy.name or "").strip()
        if not name:
            raise ValueError("评分策略名不能为空")
        cls._policies[name] = policy

    @classmethod
    def resolve(cls, name: str) -> ScorePolicy:
        normalized = str(name or "contact_weighted").strip()
        return cls._policies.get(normalized, cls._policies["contact_weighted"])
