"""
Skill 系统 — 渐进式披露架构

三层加载机制：
  Layer 1 · Index    — name + description，始终在上下文（~100 词/skill）
  Layer 2 · SKILL.md — 触发时加载完整指令（<500 行）
  Layer 3 · references/ — 按需加载的案例库、深度知识

所有 Skill 输出统一为 Pydantic JSON Schema，方便解析、存储、前端渲染。
Skills 可跨阶段复用：同一个 wechat skill 在 script 和 objection 阶段都能被加载。
"""

from .models import (
    Skill,
    SkillIndex,
    SkillPhase,
    SkillCategory,
)
from .registry import SkillRegistry, get_skill_registry
from .schemas import (
    ScenarioOutput,
    ScriptOutput,
    ObjectionOutput,
    FinalOutput,
    CopywritingResult,
    # URL 扫描流水线
    UrlProbeItem,
    InfoFinding,
    UrlScanResult,
    FindingCopywriting,
    UrlScanTask,
)

__all__ = [
    "Skill",
    "SkillIndex",
    "SkillPhase",
    "SkillCategory",
    "SkillRegistry",
    "get_skill_registry",
    "ScenarioOutput",
    "ScriptOutput",
    "ObjectionOutput",
    "FinalOutput",
    "CopywritingResult",
    "UrlProbeItem",
    "InfoFinding",
    "UrlScanResult",
    "FindingCopywriting",
    "UrlScanTask",
]
