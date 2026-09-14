"""Non-destructive global institution identity and directed hierarchy projection."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from api.dao.targets import normalize_target_name

POLICY_VERSION = 1


def _name(value: Any) -> str:
    return normalize_target_name(str(value or ""))


def project_edges(relations: list[dict], relationships: list[dict]) -> list[dict]:
    edges = []
    for relation in relations:
        parent = relation.get("parent_target_id")
        if parent:
            edges.append({
                "child_id": relation["target_id"], "parent_id": parent,
                "project_id": relation.get("project_id", ""),
                "relation_type": relation.get("relation_type", ""),
                "ownership_percent": relation.get("ownership_percent"),
                "source_urls": relation.get("source_urls") or relation.get("relation_source_urls") or (relation.get("relation") or {}).get("source_urls") or (relation.get("relation") or {}).get("relation_source_urls") or [],
                "active": relation.get("active") is not False,
                "source_id": relation.get("project_target_id", ""),
            })
    for edge in relationships:
        if edge.get("direction") != "upstream" or edge.get("relation_type") not in {
            "parent_organization", "controlled_subsidiary",
        }:
            continue
        edges.append({
            "child_id": edge.get("subject_target_id", ""),
            "parent_id": edge.get("related_target_id", ""),
            "project_id": edge.get("project_id", ""),
            "relation_type": edge.get("relation_type", ""),
            "ownership_percent": edge.get("ownership_percent"),
            "source_urls": edge.get("source_urls") or [],
            "active": edge.get("active") is not False,
            "source_id": edge.get("relationship_id", ""),
        })
    return edges


def resolve_identities(targets: list[dict], edges: list[dict]) -> tuple[dict[str, str], dict[str, list[dict]]]:
    """Match complete names against identity aliases, never two shared abbreviations.

    Existing anchors survive new aliases. Conflicting known parents and ancestor
    relationships veto a merge, including conflicts introduced transitively.
    """
    by_id = {item["target_id"]: item for item in targets}
    names = {key: _name(item.get("canonical_name")) for key, item in by_id.items()}
    parent_ids: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge["child_id"] in by_id and edge["parent_id"] in by_id:
            parent_ids[edge["child_id"]].add(edge["parent_id"])
    def ancestors(key: str) -> set[str]:
        seen, pending = set(), list(parent_ids[key])
        while pending:
            parent = pending.pop()
            if parent not in seen:
                seen.add(parent)
                pending.extend(parent_ids[parent])
        return seen
    ancestors_by_id = {key: ancestors(key) for key in by_id}
    def compatible(a: str, b: str) -> bool:
        if a in ancestors_by_id[b] or b in ancestors_by_id[a]:
            return False
        pa = {names.get(key) for key in parent_ids[a]} - {"", None}
        pb = {names.get(key) for key in parent_ids[b]} - {"", None}
        return not pa or not pb or bool(pa & pb)
    groups = {key: {key} for key in by_id}
    membership = {key: key for key in by_id}
    aliases: dict[str, set[str]] = defaultdict(set)
    for key, item in by_id.items():
        for value in [item.get("canonical_name"), item.get("display_name"), *(item.get("identity_aliases") or [])]:
            normalized = _name(value)
            # Historical aliases sometimes contain a child's full name. Such
            # an alias cannot identify the parent, even in a different project.
            child_name = any(key in ancestors_by_id[other] and names[other] == normalized for other in by_id)
            if normalized and (normalized == names[key] or not child_name):
                aliases[_name(value)].add(key)
    for key in sorted(by_id):
        for other in sorted(aliases.get(names[key], set())):
            a, b = membership[key], membership[other]
            if a == b or not all(compatible(x, y) for x in groups[a] for y in groups[b]):
                continue
            groups[a].update(groups.pop(b))
            for member in groups[a]:
                membership[member] = a
    resolved, result = {}, {}
    for members in groups.values():
        anchors = {by_id[key].get("library_identity", {}).get("canonical_target_id") for key in members} & members
        anchor = min(anchors or members, key=lambda key: (str(by_id[key].get("created_at") or "9999"), key))
        result[anchor] = [by_id[key] for key in sorted(members)]
        resolved.update({key: anchor for key in members})
    return resolved, result


def build_index(targets: list[dict], relations: list[dict], relationships: list[dict], projects: list[dict]) -> dict:
    edges = project_edges(relations, relationships)
    mapping, groups = resolve_identities(targets, edges)
    project_names = {str(item.get("_id") or item.get("id")): item.get("name", "历史项目") for item in projects}
    rows = {}
    for anchor, members in groups.items():
        canonical = next(item for item in members if item["target_id"] == anchor)
        rows[anchor] = {
            "target_id": anchor, "target_name": canonical.get("canonical_name") or anchor,
            "member_target_ids": [item["target_id"] for item in members],
            "aliases": sorted({str(value) for item in members for value in [item.get("canonical_name"), item.get("display_name"), *(item.get("identity_aliases") or [])] if value}),
            "projects": [], "relationships": [], "related_units": [], "parent_target_id": "", "parent_target_name": "",
            "child_count": 0, "merged_count": len(members), "relation_conflict": False,
        }
    grouped_relations: dict[str, list[dict]] = defaultdict(list)
    for relation in relations:
        anchor = mapping.get(relation.get("target_id"))
        if anchor:
            grouped_relations[anchor].append(relation)
    for anchor, entries in grouped_relations.items():
        pids = sorted({item.get("project_id", "") for item in entries} - {""})
        rows[anchor]["projects"] = [{"project_id": pid, "project_name": project_names.get(pid, "历史项目（" + pid + "）"), "active": any(item.get("active") is not False and item.get("project_id") == pid for item in entries)} for pid in pids]
    for edge in edges:
        child, parent = mapping.get(edge["child_id"]), mapping.get(edge["parent_id"])
        if child and parent and child != parent:
            rows[child]["relationships"].append({**edge, "parent_id": parent, "child_id": child, "parent_name": rows[parent]["target_name"], "project_name": project_names.get(edge["project_id"], "历史项目")})
    for edge in relationships:
        subject, related = mapping.get(edge.get("subject_target_id")), mapping.get(edge.get("related_target_id"))
        if subject and related and subject != related:
            rows[subject]["related_units"].append({"target_id": related, "target_name": rows[related]["target_name"], "direction": edge.get("direction"), "relation_type": edge.get("relation_type"), "summary": edge.get("summary"), "project_id": edge.get("project_id"), "source_urls": edge.get("source_urls") or [], "active": edge.get("active") is not False})
    parents = {}
    for anchor, row in rows.items():
        candidates = sorted(row["relationships"], key=lambda edge: (not edge["active"], edge["parent_id"], edge["source_id"]))
        row["relation_conflict"] = len({edge["parent_id"] for edge in candidates if edge["active"]}) > 1
        for edge in candidates:
            current, visited = edge["parent_id"], {anchor}
            while current and current not in visited:
                visited.add(current)
                current = parents.get(current, "")
            if current:
                continue
            parents[anchor] = edge["parent_id"]
            row["parent_target_id"] = edge["parent_id"]
            row["parent_target_name"] = edge["parent_name"]
            rows[edge["parent_id"]]["child_count"] += 1
            break
    return {"rows": rows, "mapping": mapping, "relations": grouped_relations, "targets": targets}
