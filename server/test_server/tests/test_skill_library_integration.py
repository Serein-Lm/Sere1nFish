from pathlib import Path

import pytest

from api.services.skill_library.filesystem import FilesystemSkillSourceAdapter
from api.services.skill_library.selection import (
    current_selected_skill_ids,
    normalize_selected_skill_ids,
    skill_selection,
    validate_selected_skill_ids,
)
from Sere1nGraph.graph.skills import registry as registry_module
from Sere1nGraph.graph.skills.registry import SkillRegistry
from Sere1nGraph.graph.tools.skill_tools import (
    list_available_skills,
    load_skill,
    load_skill_resource,
)


def test_filesystem_skill_source_builds_progressive_resource_tree(tmp_path: Path) -> None:
    skill_dir = tmp_path / "docx"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: Word 文档\n"
        "description_zh: 生成高质量 Word 文档\n"
        "phases: [finalize]\n"
        "---\n"
        "# 执行规范\n",
        encoding="utf-8",
    )
    (skill_dir / "references" / "layout.md").write_text("版式规则", encoding="utf-8")
    (skill_dir / "scripts" / "render.py").write_text("print('ok')", encoding="utf-8")
    (skill_dir / "scripts" / "ignored.pyc").write_bytes(b"ignored")

    adapter = FilesystemSkillSourceAdapter(
        root=tmp_path,
        source_key="test",
        display_name="test-source",
        default_category="document-output",
    )
    packages = adapter.discover()

    assert len(packages) == 1
    package = packages[0]
    assert package.slug == "docx"
    assert package.content_raw == "# 执行规范"
    assert package.meta["progressive_disclosure"] is True
    resources = {resource.path: resource for resource in package.resources}
    assert resources["SKILL.md"].role == "instruction"
    assert resources["references"].kind == "directory"
    assert resources["references/layout.md"].role == "reference"
    assert resources["scripts/render.py"].role == "script"
    assert "scripts/ignored.pyc" not in resources


def test_skill_selection_is_request_scoped_and_normalized() -> None:
    assert normalize_selected_skill_ids(["docx", "docx", "pdf"]) == ["docx", "pdf"]
    assert current_selected_skill_ids() is None

    with skill_selection(["docx"]):
        assert current_selected_skill_ids() == frozenset({"docx"})

    assert current_selected_skill_ids() is None
    with pytest.raises(ValueError, match="格式无效"):
        normalize_selected_skill_ids(["../secret"])


def test_agent_skill_tools_enforce_selection_and_load_resources(monkeypatch) -> None:
    registry = SkillRegistry()
    registry.load_from_documents([
        {
            "slug": "docx",
            "name": "Word 文档",
            "description": "生成 Word",
            "category": "document-output",
            "status": "approved",
            "content_raw": "先组织内容，再渲染。",
            "meta": {
                "phases": ["finalize"],
                "resource_contents": {
                    "references/layout.md": {
                        "role": "reference",
                        "content": "使用清晰的标题层级。",
                    },
                    "scripts/render.py": {
                        "role": "script",
                        "content": "print('render')",
                    },
                },
            },
        },
        {
            "slug": "pdf",
            "name": "PDF 文档",
            "description": "生成 PDF",
            "category": "document-output",
            "status": "approved",
            "content_raw": "PDF 指令",
            "meta": {"phases": ["finalize"]},
        },
    ])
    monkeypatch.setattr(registry_module, "_registry", registry)

    with skill_selection(["docx"]):
        index = list_available_skills.invoke({})
        assert "[docx]" in index
        assert "[pdf]" not in index
        assert "先组织内容" in load_skill.invoke({"skill_id": "docx"})
        assert "scripts/render.py" in load_skill.invoke({"skill_id": "docx"})
        assert "print('render')" in load_skill_resource.invoke({
            "skill_id": "docx",
            "resource_path": "scripts/render.py",
        })
        assert "不在本轮用户选择范围" in load_skill.invoke({"skill_id": "pdf"})


def test_selected_skill_validation_rejects_disabled_skill(monkeypatch) -> None:
    registry = SkillRegistry()
    registry.load_from_documents([
        {
            "slug": "disabled-docx",
            "name": "已停用 Word 文档",
            "description": "不应再被任务选择",
            "category": "document-output",
            "status": "approved",
            "enabled": False,
            "content_raw": "停用指令",
        },
    ])
    monkeypatch.setattr(registry_module, "_registry", registry)

    assert "disabled-docx" not in list_available_skills.invoke({})
    assert "不存在" in load_skill.invoke({"skill_id": "disabled-docx"})
    with pytest.raises(ValueError, match="不存在或未通过审核"):
        validate_selected_skill_ids(["disabled-docx"])
