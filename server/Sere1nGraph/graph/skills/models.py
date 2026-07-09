"""
Skill 数据模型 — 渐进式披露三层架构

Layer 1 · SkillIndex  — name + description，始终在上下文
Layer 2 · SKILL.md    — 触发时加载的完整指令
Layer 3 · references/ — 按需加载的案例库/深度知识

目录结构约定：
  skills/
    <skill-id>/
      SKILL.md          # Layer 2: 完整指令（YAML frontmatter + Markdown body）
      references/       # Layer 3: 按需加载
        cases.md
        ...

Skill 可跨阶段复用：phases 字段是列表，一个 skill 可以同时服务于 scenario + objection。
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class SkillPhase(str, Enum):
    """话术生成阶段"""
    SCENARIO = "scenario"
    SCRIPT = "script"
    OBJECTION = "objection"
    FINALIZE = "finalize"


class SkillCategory(str, Enum):
    """Skill 类别 — 按渠道/场景分类"""
    GENERAL = "general"
    REAL_CASES = "real_cases"
    WECHAT = "wechat"
    EMAIL = "email"
    PHONE = "phone"
    INTRANET = "intranet"
    SMS = "sms"
    RECRUITMENT = "recruitment"
    VENDOR = "vendor"
    IT_SUPPORT = "it_support"
    CUSTOMER = "customer"
    GOVERNMENT = "government"
    FINANCE = "finance"


class SkillIndex(BaseModel):
    """
    Layer 1 — 索引元数据（始终在上下文，~100 词）

    这是 SKILL.md frontmatter 解析出来的轻量摘要。
    Registry 只持有这一层，不加载 body。
    """
    id: str = Field(description="唯一标识，即目录名")
    name: str = Field(description="显示名称")
    description: str = Field(description="触发描述 — 什么时候该用这个 skill")
    category: SkillCategory | str = Field(description="类别")
    phases: list[SkillPhase] = Field(description="适用的阶段（可多个，实现复用）")
    tags: list[str] = Field(default_factory=list, description="搜索标签")
    priority: int = Field(default=5, description="优先级 1-10，越小越优先")
    enabled: bool = Field(default=True)
    # Layer 2/3 的路径（相对于 skills/ 目录）
    skill_dir: str = Field(default="", description="skill 目录路径")

    class Config:
        use_enum_values = True


class Skill(BaseModel):
    """
    Layer 1 + Layer 2 — 完整 Skill（触发后加载）

    index: 元数据
    body:  SKILL.md 的 Markdown 正文（指令部分）
    references: Layer 3 文件名列表（按需再加载）
    """
    index: SkillIndex
    body: str = Field(default="", description="SKILL.md body — 完整指令")
    references: list[str] = Field(default_factory=list, description="references/ 下的文件名列表")
    reference_contents: dict[str, str] = Field(
        default_factory=dict,
        description="数据库来源的 reference 内容，文件来源为空",
    )

    # ── 便捷属性 ──

    @property
    def id(self) -> str:
        return self.index.id

    @property
    def name(self) -> str:
        return self.index.name

    @property
    def phases(self) -> list[SkillPhase]:
        return self.index.phases

    @property
    def category(self) -> SkillCategory:
        return self.index.category

    def serves_phase(self, phase: SkillPhase) -> bool:
        """这个 skill 是否服务于指定阶段"""
        return phase in self.index.phases or phase.value in self.index.phases

    def load_reference(self, ref_name: str) -> str:
        """Layer 3: 按需加载某个 reference 文件"""
        if ref_name in self.reference_contents:
            return self.reference_contents[ref_name]
        from pathlib import Path
        ref_path = Path(self.index.skill_dir) / "references" / ref_name
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8")
        return ""
