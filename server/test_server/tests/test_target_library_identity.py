from api.services.target_library.identity import build_index
from api.services.target_library.scans import scan_index, scan_summary


def target(key, name, **extra):
    return {"target_id": key, "canonical_name": name, **extra}


def test_complete_name_variant_merges_cross_project_without_merging_shared_short_names():
    targets = [target("n1", "国家卫生健康委员会"), target("n2", "中华人民共和国国家卫生健康委员会", display_name="国家卫生健康委员会"), target("f", "福建省水利厅", display_name="水利厅"), target("j", "江西省水利厅", display_name="水利厅")]
    index = build_index(targets, [{"target_id": "n1", "project_id": "p1"}, {"target_id": "n2", "project_id": "p2", "active": False}], [], [])
    assert len(index["rows"]) == 3
    row = index["rows"][index["mapping"]["n1"]]
    assert set(row["member_target_ids"]) == {"n1", "n2"}
    assert {p["project_id"] for p in row["projects"]} == {"p1", "p2"}
    assert index["mapping"]["f"] != index["mapping"]["j"]


def test_different_parents_and_ancestor_aliases_cannot_merge():
    targets = [target("p", "甲集团", identity_aliases=["甲子公司"]), target("q", "乙集团"), target("a", "甲子公司"), target("b", "甲子公司")]
    relations = [{"target_id": "a", "parent_target_id": "p"}, {"target_id": "b", "parent_target_id": "q"}]
    index = build_index(targets, relations, [], [])
    assert len(index["rows"]) == 4
    assert index["rows"]["a"]["parent_target_id"] == "p"


def test_identity_anchor_and_cycle_projection_preserve_all_original_units():
    targets = [target("a", "同一机构", library_identity={"canonical_target_id": "b"}), target("b", "同一机构", library_identity={"canonical_target_id": "b"}), target("c", "其他机构"), target("d", "第四机构")]
    index = build_index(targets, [{"target_id": "c", "parent_target_id": "d"}, {"target_id": "d", "parent_target_id": "c"}], [], [])
    assert index["mapping"]["a"] == "b"
    for key in index["rows"]:
        seen = set()
        while key:
            assert key not in seen
            seen.add(key)
            key = index["rows"][key]["parent_target_id"]


def test_multi_target_mobile_window_is_scoped_and_waiting_is_not_a_completed_scan():
    index = build_index([target("a", "机构甲"), target("b", "机构乙")], [], [], [])
    tasks = [{"task_id": "run", "project_id": "p", "params": {}, "status": "running", "started_at": "2026-09-14T10:00:00Z", "progress": {"stage": "waiting_mobile"}}]
    windows = [{"run_task_id": "run", "until": "2026-09-14T10:00:00Z", "targets": {"a": {"since": "2026-09-01T00:00:00Z"}, "b": {"since": "2026-09-05T00:00:00Z"}}}]
    scans = scan_index(index, tasks, [], windows)
    assert set(scans["a"][0]["incremental_targets"]) == {"a"}
    summary = scan_summary(index["rows"]["a"], [], scans["a"])
    assert summary["scan_count"] == 1
    assert summary["first_scan_at"] is None
    assert summary["last_success_at"] is None
