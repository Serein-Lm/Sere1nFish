"""Project merge migration helper tests."""

from __future__ import annotations

from bson import ObjectId


def test_replace_project_references_handles_nested_links_and_deduplicates() -> None:
    from scripts.merge_projects import replace_project_references

    target = "111111111111111111111111"
    source = "222222222222222222222222"
    document = {
        "project_id": source,
        "project_ids": [target, source],
        "project_links": [{"project_id": source, "finding_id": "finding-1"}],
        "data": {"findings": [{"project_id": source}]},
        "unrelated": f"prefix-{source}",
        "unrelated_exact": source,
    }

    updated = replace_project_references(document, {source}, target)

    assert updated["project_id"] == target
    assert updated["project_ids"] == [target]
    assert updated["project_links"][0]["project_id"] == target
    assert updated["data"]["findings"][0]["project_id"] == target
    assert updated["unrelated"] == f"prefix-{source}"
    assert updated["unrelated_exact"] == source


def test_project_id_discovery_ignores_non_object_id_observation_names() -> None:
    from scripts.merge_projects import _project_ids

    found = _project_ids(
        {
            "project_id": "obs-test-123",
            "project_ids": ["222222222222222222222222", "not-an-object-id"],
            "nested": {"project_id": "111111111111111111111111"},
        }
    )

    assert found == {
        "222222222222222222222222",
        "111111111111111111111111",
    }


def test_project_reference_helpers_preserve_object_id_type() -> None:
    from scripts.merge_projects import _project_ids, replace_project_references

    target = "111111111111111111111111"
    source = "222222222222222222222222"
    document = {
        "project_id": ObjectId(source),
        "nested": {"project_ids": [ObjectId(source), source]},
    }

    assert _project_ids(document) == {source}

    updated = replace_project_references(document, {source}, target)

    assert updated["project_id"] == ObjectId(target)
    assert updated["nested"]["project_ids"] == [ObjectId(target), target]
