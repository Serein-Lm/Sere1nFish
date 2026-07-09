"""
Skill Registry — 索引 + 按需加载

设计：
- Registry 只持有 Layer 1（SkillIndex），内存占用极小
- 查询时返回 index 列表，供 LLM/workflow 决策
- 需要完整指令时才加载 Layer 2（SKILL.md body）
- 需要案例/深度知识时才加载 Layer 3（references/）

加载源：
- 运行时：MongoDB skills 集合快照（Layer 1 常驻，Layer 2/3 按需从内存快照取）
- 同步入口：由服务启动和 CRUD 写操作刷新快照
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from .models import Skill, SkillIndex, SkillPhase, SkillCategory


# ── YAML frontmatter 解析（轻量，不依赖 pyyaml）──

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_KV_RE = re.compile(r"^(\w+)\s*:\s*(.+)$", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^\s*-\s*(.+)$", re.MULTILINE)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 SKILL.md 的 YAML frontmatter，返回 (metadata_dict, body)"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    raw_yaml = m.group(1)
    body = text[m.end():]

    meta: dict = {}
    # 先找所有 key: value
    for kv in _KV_RE.finditer(raw_yaml):
        key, val = kv.group(1), kv.group(2).strip()
        # 检查是否是列表开头（值为空或就是 list 标记）
        if val == "" or val.startswith("["):
            # 尝试解析 inline list [a, b, c]
            if val.startswith("[") and val.endswith("]"):
                items = [i.strip().strip("'\"") for i in val[1:-1].split(",") if i.strip()]
                meta[key] = items
                continue
        meta[key] = val

    # 解析 block-style lists（phases:, tags: 后面跟 - item）
    sections = re.split(r"\n(?=\w)", raw_yaml)
    for section in sections:
        lines = section.strip().split("\n")
        if not lines:
            continue
        header = lines[0]
        if ":" in header:
            key = header.split(":")[0].strip()
            items = _LIST_ITEM_RE.findall(section)
            if items:
                meta[key] = [i.strip().strip("'\"") for i in items]

    return meta, body


class SkillRegistry:
    """
    Skill 注册表

    只持有 SkillIndex（Layer 1），按需加载 body 和 references。
    """

    def __init__(self):
        self._index: dict[str, SkillIndex] = {}  # skill_id -> SkillIndex
        self._skills_base_dir: Optional[Path] = None
        self._bodies: dict[str, str] = {}
        self._reference_contents: dict[str, dict[str, str]] = {}

    # ── 注册 ──

    def register(self, index: SkillIndex) -> None:
        """注册一个 skill 的索引"""
        self._index[index.id] = index

    def clear(self) -> None:
        """清空运行时快照。"""
        self._index.clear()
        self._bodies.clear()
        self._reference_contents.clear()

    @staticmethod
    def _parse_phases(raw: Any) -> list[SkillPhase]:
        values = raw
        if not values:
            values = [phase.value for phase in SkillPhase]
        if isinstance(values, str):
            values = [v.strip() for v in values.split(",") if v.strip()]
        phases: list[SkillPhase] = []
        for value in values or []:
            try:
                phases.append(SkillPhase(value))
            except ValueError:
                continue
        return phases or [phase for phase in SkillPhase]

    @staticmethod
    def _parse_references(meta: dict[str, Any]) -> dict[str, str]:
        raw = meta.get("references") or meta.get("reference_contents") or {}
        if isinstance(raw, dict):
            return {
                str(name): str(content)
                for name, content in raw.items()
                if str(name).strip() and content is not None
            }
        return {}

    def register_from_document(self, doc: dict[str, Any]) -> Optional[SkillIndex]:
        """从 MongoDB skill 文档注册 Layer 1，并缓存 Layer 2/3。"""
        slug = str(doc.get("slug") or doc.get("skill_id") or "").strip()
        name = str(doc.get("name") or slug).strip()
        if not slug or not name:
            return None

        meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        phases = self._parse_phases(doc.get("phases") or meta.get("phases"))
        tags = doc.get("tags") if isinstance(doc.get("tags"), list) else []
        priority = int(doc.get("priority") or 5)
        status = str(doc.get("status") or "approved")
        enabled = status == "approved" and bool(doc.get("enabled", True))

        idx = SkillIndex(
            id=slug,
            name=name,
            description=str(doc.get("description") or ""),
            category=str(doc.get("category") or "general"),
            phases=phases,
            tags=[str(tag) for tag in tags if str(tag).strip()],
            priority=priority,
            enabled=enabled,
            skill_dir=str(meta.get("source_path") or ""),
        )
        self.register(idx)
        self._bodies[slug] = str(doc.get("content_raw") or "")
        self._reference_contents[slug] = self._parse_references(meta)
        return idx

    def load_from_documents(self, docs: list[dict[str, Any]]) -> int:
        """用数据库文档替换当前 registry 快照。"""
        self.clear()
        count = 0
        for doc in docs:
            if self.register_from_document(doc):
                count += 1
        return count

    def register_from_dir(self, skill_dir: Path) -> Optional[SkillIndex]:
        """从 skill 目录加载并注册（只读 frontmatter，不读 body）"""
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None

        text = skill_md.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(text)

        if not meta.get("name"):
            return None

        # 解析 phases
        raw_phases = meta.get("phases", [])
        if isinstance(raw_phases, str):
            raw_phases = [raw_phases]
        phases = []
        for p in raw_phases:
            try:
                phases.append(SkillPhase(p))
            except ValueError:
                pass

        # 解析 category
        try:
            category = SkillCategory(meta.get("category", "general"))
        except ValueError:
            category = SkillCategory.GENERAL

        # 解析 tags
        raw_tags = meta.get("tags", [])
        if isinstance(raw_tags, str):
            raw_tags = [t.strip() for t in raw_tags.split(",")]

        idx = SkillIndex(
            id=skill_dir.name,
            name=meta["name"],
            description=meta.get("description", ""),
            category=category,
            phases=phases,
            tags=raw_tags,
            priority=int(meta.get("priority", 5)),
            enabled=meta.get("enabled", "true") != "false",
            skill_dir=str(skill_dir),
        )
        self.register(idx)
        return idx

    def scan_directory(self, base_dir: Optional[Path] = None) -> int:
        """扫描目录，注册所有 skill"""
        base = base_dir or self._default_skills_dir()
        self._skills_base_dir = base
        count = 0
        if not base.exists():
            return 0
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / "SKILL.md").exists():
                if self.register_from_dir(child):
                    count += 1
        return count

    # ── 查询（Layer 1 — 只返回索引）──

    def list_all(self) -> list[SkillIndex]:
        """列出所有 skill 索引"""
        return sorted(self._index.values(), key=lambda s: (s.priority, s.id))

    def list_by_phase(self, phase: SkillPhase) -> list[SkillIndex]:
        """列出某阶段可用的 skills"""
        return sorted(
            [s for s in self._index.values()
             if phase in s.phases or phase.value in [p.value if isinstance(p, SkillPhase) else p for p in s.phases]],
            key=lambda s: s.priority,
        )

    def list_by_category(self, category: SkillCategory) -> list[SkillIndex]:
        """列出某类别的 skills"""
        cat_val = category.value if isinstance(category, SkillCategory) else category
        return sorted(
            [s for s in self._index.values()
             if (s.category.value if isinstance(s.category, SkillCategory) else s.category) == cat_val],
            key=lambda s: s.priority,
        )

    def search(self, query: str) -> list[SkillIndex]:
        """按关键词搜索（匹配 name/description/tags）"""
        q = query.lower()
        results = []
        for s in self._index.values():
            text = f"{s.name} {s.description} {' '.join(s.tags)}".lower()
            if q in text:
                results.append(s)
        return sorted(results, key=lambda s: s.priority)

    def get_index(self, skill_id: str) -> Optional[SkillIndex]:
        """获取单个 skill 索引"""
        return self._index.get(skill_id)

    # ── 加载（Layer 2 + 3）──

    def load_skill(self, skill_id: str) -> Optional[Skill]:
        """加载完整 skill（Layer 1 + 2 + 3 文件列表）"""
        idx = self._index.get(skill_id)
        if not idx:
            return None

        skill_dir = Path(idx.skill_dir) if idx.skill_dir else None
        body = self._bodies.get(skill_id, "")
        ref_map = self._reference_contents.get(skill_id, {})
        refs: list[str] = sorted(ref_map)

        if not body and skill_dir and skill_dir.exists():
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                text = skill_md.read_text(encoding="utf-8")
                _, body = _parse_frontmatter(text)

            ref_dir = skill_dir / "references"
            if ref_dir.exists():
                refs = sorted([f.name for f in ref_dir.iterdir() if f.is_file()])

        return Skill(
            index=idx,
            body=body,
            references=refs,
            reference_contents=ref_map,
        )

    def load_skills_for_phase(
        self,
        phase: SkillPhase,
        categories: list[SkillCategory] | None = None,
    ) -> list[Skill]:
        """加载某阶段所有匹配的 skills（Layer 2）"""
        indices = self.list_by_phase(phase)

        if categories:
            cat_vals = {c.value if isinstance(c, SkillCategory) else c for c in categories}
            # 始终包含 general 和 real_cases
            cat_vals.add("general")
            cat_vals.add("real_cases")
            indices = [
                s for s in indices
                if (s.category.value if isinstance(s.category, SkillCategory) else s.category) in cat_vals
            ]

        skills = []
        for idx in indices:
            if not idx.enabled:
                continue
            skill = self.load_skill(idx.id)
            if skill:
                skills.append(skill)
        return skills

    # ── 摘要 ──

    def get_summary(self) -> dict:
        """注册表摘要"""
        summary = {"total": len(self._index), "by_phase": {}, "by_category": {}}
        for phase in SkillPhase:
            summary["by_phase"][phase.value] = len(self.list_by_phase(phase))
        for cat in SkillCategory:
            summary["by_category"][cat.value] = len(self.list_by_category(cat))
        return summary

    def get_index_prompt(self) -> str:
        """
        生成 Layer 1 索引 prompt — 供 LLM 查看所有可用 skills

        格式：每个 skill 一行，name + description + phases + tags
        """
        lines = []
        for s in self.list_all():
            phases_str = ",".join(
                p.value if isinstance(p, SkillPhase) else p for p in s.phases
            )
            tags_str = ",".join(s.tags) if s.tags else ""
            lines.append(
                f"- [{s.id}] {s.name}: {s.description} "
                f"(phases={phases_str}, category={s.category if isinstance(s.category, str) else s.category.value}"
                f"{', tags=' + tags_str if tags_str else ''})"
            )
        return "\n".join(lines)

    # ── 内部 ──

    @staticmethod
    def _default_skills_dir() -> Path:
        return Path(__file__).parent / "library"


# ── 全局单例 ──

_registry: Optional[SkillRegistry] = None


def get_skill_registry() -> SkillRegistry:
    """获取全局 Skill 注册表。运行时数据由 MongoDB 刷新入口注入。"""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry
