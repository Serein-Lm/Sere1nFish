"""Read-only filesystem adapter for portable Skill packages."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any

import yaml

from .contracts import SkillPackage, SkillResourceSpec


_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n", re.DOTALL)
_IGNORED_PARTS = {"__pycache__", ".git", "node_modules", "dist"}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_PACKAGE_BYTES = 8 * 1024 * 1024
_MAX_PACKAGE_FILES = 1000


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(value).strip()]


def _resource_role(path: str) -> str:
    root = path.split("/", 1)[0].casefold()
    if root in {"reference", "references"}:
        return "reference"
    if root == "scripts":
        return "script"
    if root in {"template", "templates"}:
        return "template"
    return "resource"


def _parse_skill_markdown(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = yaml.safe_load(match.group(1)) or {}
    if not isinstance(raw, dict):
        raise ValueError("SKILL.md frontmatter 必须是对象")
    return {str(key): value for key, value in raw.items()}, text[match.end():]


class FilesystemSkillSourceAdapter:
    """Discover immediate child directories containing a SKILL.md file."""

    def __init__(
        self,
        *,
        root: Path,
        source_key: str,
        display_name: str,
        default_category: str = "general",
        default_phases: tuple[str, ...] = ("finalize",),
    ) -> None:
        self.root = root
        self.source_key = source_key
        self.display_name = display_name
        self.default_category = default_category
        self.default_phases = default_phases

    def available(self) -> bool:
        return self.root.is_dir()

    def discover(self) -> list[SkillPackage]:
        if not self.available():
            return []
        packages: list[SkillPackage] = []
        for directory in sorted(self.root.iterdir(), key=lambda item: item.name):
            if directory.is_dir() and not directory.is_symlink() and (directory / "SKILL.md").is_file():
                packages.append(self._read_package(directory))
        return packages

    def _read_package(self, directory: Path) -> SkillPackage:
        skill_path = directory / "SKILL.md"
        source_text = skill_path.read_text(encoding="utf-8")
        metadata, body = _parse_skill_markdown(source_text)
        slug = directory.name
        category = str(metadata.get("category") or self.default_category)
        resources = self._read_resources(directory)
        resource_bytes = sum(item.size for item in resources if item.kind == "file")
        manifest_hash = hashlib.sha256(
            "\n".join(
                f"{item.path}:{item.content_hash}" for item in resources if item.kind == "file"
            ).encode("utf-8")
        ).hexdigest()
        phases = _as_list(metadata.get("phases")) or list(self.default_phases)
        tags = sorted({category, slug, "artifact", *_as_list(metadata.get("tags"))})
        file_signals = _as_list(metadata.get("file_signals"))
        if not file_signals and slug in {"docx", "pdf", "pptx", "xlsx"}:
            file_signals = [f".{slug}"]
        description = str(
            metadata.get("description_zh")
            or metadata.get("description")
            or ""
        ).strip()
        return SkillPackage(
            slug=slug,
            name=str(metadata.get("name") or slug).strip(),
            description=description,
            content_raw=body.strip(),
            category=category,
            tags=tags,
            triggers=_as_list(metadata.get("triggers")),
            anti_triggers=_as_list(metadata.get("anti_triggers")),
            aliases=_as_list(metadata.get("aliases")),
            requires=_as_list(metadata.get("requires")),
            related=_as_list(metadata.get("related")),
            file_signals=file_signals,
            risk_signals=_as_list(metadata.get("risk_signals")),
            priority=int(metadata.get("priority") or 5),
            meta={
                "source": self.display_name,
                "source_key": self.source_key,
                "source_path": f"{slug}/SKILL.md",
                "source_version": str(metadata.get("version") or ""),
                "license": str(metadata.get("license") or ""),
                "description_original": str(metadata.get("description") or ""),
                "phases": phases,
                "resource_count": len(resources),
                "resource_bytes": resource_bytes,
                "manifest_hash": manifest_hash,
                "progressive_disclosure": True,
            },
            resources=resources,
        )

    def _read_resources(self, directory: Path) -> list[SkillResourceSpec]:
        files = [
            path
            for path in directory.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and not any(part in _IGNORED_PARTS for part in path.relative_to(directory).parts)
            and path.suffix.casefold() not in _IGNORED_SUFFIXES
        ]
        if len(files) > _MAX_PACKAGE_FILES:
            raise ValueError(f"Skill {directory.name} 文件数超过 {_MAX_PACKAGE_FILES}")

        resources: list[SkillResourceSpec] = []
        directory_paths: set[str] = set()
        total_bytes = 0
        for path in sorted(files):
            relative = path.relative_to(directory).as_posix()
            for parent in Path(relative).parents:
                parent_value = parent.as_posix()
                if parent_value != ".":
                    directory_paths.add(parent_value)
            data = path.read_bytes()
            if len(data) > _MAX_FILE_BYTES:
                raise ValueError(f"Skill 资源过大: {directory.name}/{relative}")
            total_bytes += len(data)
            if total_bytes > _MAX_PACKAGE_BYTES:
                raise ValueError(f"Skill {directory.name} 总资源超过 {_MAX_PACKAGE_BYTES} bytes")
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                content = None
            resources.append(
                SkillResourceSpec(
                    path=relative,
                    parent_path=(
                        "" if Path(relative).parent.as_posix() == "."
                        else Path(relative).parent.as_posix()
                    ),
                    name=path.name,
                    kind="file",
                    role="instruction" if relative == "SKILL.md" else _resource_role(relative),
                    content_type=mimetypes.guess_type(path.name)[0] or "text/plain",
                    size=len(data),
                    content_hash=hashlib.sha256(data).hexdigest(),
                    content=content,
                )
            )

        for relative in sorted(directory_paths):
            path = Path(relative)
            parent = path.parent.as_posix()
            resources.append(
                SkillResourceSpec(
                    path=relative,
                    parent_path="" if parent == "." else parent,
                    name=path.name,
                    kind="directory",
                    role=_resource_role(relative),
                    content_type="inode/directory",
                    size=0,
                    content_hash="",
                )
            )
        return sorted(resources, key=lambda item: (item.parent_path, item.kind != "directory", item.name))
