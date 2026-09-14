"""Read-time procurement ownership based on named parties and project identities."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from api.dao.targets import normalize_target_name


_PARTY_FIELDS = ("purchaser", "agency", "winner")
_IDENTITY_FIELDS = ("target_name", "canonical_name", "identity_aliases", "display_name", "short_names")
_ROLE_PREFIX = re.compile(r"^(?:采购人|招标人|采购单位|招标单位|代理机构|中标人|中标单位|成交供应商)\s*[:：]\s*")
_PARTY_SEPARATOR = re.compile(r"[、；;\n|]+")
_GENERIC_NAMES = {"公司", "集团", "有限公司", "股份有限公司", "中心", "管理中心", "机场", "集团公司"}


def _names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return _names(value.get("name") or value.get("company_name"))
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [name for item in value for name in _names(item)]
    return []


class BiddingOwnershipScope:
    """Resolve announcement parties without treating a query hit as ownership.

    Search aliases and arbitrary body/domain matches deliberately carry no
    ownership weight. Descendants must already exist in the project hierarchy.
    """

    def __init__(self, relations: Sequence[Mapping[str, Any]]) -> None:
        self.relations = {
            str(item["target_id"]): item
            for item in relations
            if item.get("target_id") and item.get("active") is not False
        }
        self.aliases: dict[str, set[str]] = defaultdict(set)
        for target_id, relation in self.relations.items():
            for field in _IDENTITY_FIELDS:
                for name in _names(relation.get(field)):
                    normalized = normalize_target_name(name)
                    if len(normalized) >= 3 and normalized not in _GENERIC_NAMES:
                        self.aliases[normalized].add(target_id)

    def target_ids(self, target_id: str = "") -> set[str]:
        if not target_id:
            return set(self.relations)
        if target_id not in self.relations:
            return set()
        selected = {target_id}
        for candidate_id, relation in self.relations.items():
            ancestors = {str(value) for value in relation.get("lineage_target_ids") or []}
            ancestors.add(str(relation.get("root_target_id") or ""))
            if target_id in ancestors:
                selected.add(candidate_id)
                continue
            current_id = candidate_id
            visited: set[str] = set()
            while current_id in self.relations and current_id not in visited:
                if current_id == target_id:
                    selected.add(candidate_id)
                    break
                visited.add(current_id)
                current_id = str(self.relations[current_id].get("parent_target_id") or "")
        return selected

    def owner_ids(self, record: Mapping[str, Any]) -> list[str]:
        owners: set[str] = set()
        for field in _PARTY_FIELDS:
            for raw_name in _names(record.get(field)):
                # Keep the complete value as well, so punctuation in an English
                # legal name is not confused with a multi-party separator.
                for name in [raw_name, *_PARTY_SEPARATOR.split(raw_name)]:
                    normalized = normalize_target_name(_ROLE_PREFIX.sub("", name.strip()))
                    owners.update(self.aliases.get(normalized, ()))
        return sorted(owners)


__all__ = ["BiddingOwnershipScope"]
