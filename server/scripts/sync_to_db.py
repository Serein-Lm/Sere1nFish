#!/usr/bin/env python3
"""
一键数据同步脚本：skills + prompts → MongoDB

用法:
    cd Sere1nFishServer
    python -m scripts.sync_to_db                # 同步 skills/prompts（不覆盖已有）
    python -m scripts.sync_to_db --overwrite     # 强制覆盖
    python -m scripts.sync_to_db --only skills   # 仅同步 skills
    python -m scripts.sync_to_db --only prompts  # 仅同步 prompts

依赖:
    MongoDB 连接读取环境变量/bootstrap settings
    脚本内置的 SKILL_MANIFEST（skills 数据源，对齐 BaJie-MCP 170 个 skill）
    脚本内置的 PROMPT_SEEDS（prompts 种子数据）
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

# 把项目根目录加入 sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from motor.motor_asyncio import AsyncIOMotorClient


SKILLS_LIBRARY_DIR = ROOT / "Sere1nGraph" / "graph" / "skills" / "library"
PROMPTS_DIR = ROOT / "Sere1nGraph" / "graph" / "prompts"


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_KV_RE = re.compile(r"^(\w+)\s*:\s*(.*)$", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^\s*-\s*(.+)$", re.MULTILINE)


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse the simple YAML frontmatter used by local SKILL.md files."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    raw_yaml = match.group(1)
    body = text[match.end():]
    meta: dict[str, object] = {}

    for kv in _KV_RE.finditer(raw_yaml):
        key, val = kv.group(1), kv.group(2).strip()
        if val.startswith("[") and val.endswith("]"):
            meta[key] = [
                item.strip().strip("'\"")
                for item in val[1:-1].split(",")
                if item.strip()
            ]
        elif val:
            meta[key] = val.strip("'\"")

    sections = re.split(r"\n(?=\w)", raw_yaml)
    for section in sections:
        lines = section.strip().split("\n")
        if not lines or ":" not in lines[0]:
            continue
        key = lines[0].split(":", 1)[0].strip()
        items = _LIST_ITEM_RE.findall(section)
        if items:
            meta[key] = [item.strip().strip("'\"") for item in items]

    return meta, body


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(value).strip()]


def _load_skill_references(slug: str) -> dict[str, str]:
    ref_dir = SKILLS_LIBRARY_DIR / slug / "references"
    if not ref_dir.exists():
        return {}
    refs: dict[str, str] = {}
    for path in sorted(ref_dir.iterdir()):
        if path.is_file():
            refs[path.name] = path.read_text(encoding="utf-8")
    return refs


# ═══════════════════════════════════════════
#  数据库连接（独立于业务运行时配置；读取环境变量/bootstrap settings）
# ═══════════════════════════════════════════

def _get_db():
    from api.config import get_settings

    settings = get_settings()
    kwargs = {
        "authSource": settings.MONGODB_AUTH_SOURCE,
        "appname": settings.MONGODB_APPNAME,
        "maxPoolSize": settings.MONGODB_MAX_POOL_SIZE,
        "minPoolSize": settings.MONGODB_MIN_POOL_SIZE,
        "maxIdleTimeMS": settings.MONGODB_MAX_IDLE_TIME_MS,
        "serverSelectionTimeoutMS": settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS,
        "connectTimeoutMS": settings.MONGODB_CONNECT_TIMEOUT_MS,
    }
    if settings.MONGODB_USERNAME:
        kwargs["username"] = settings.MONGODB_USERNAME
    if settings.MONGODB_PASSWORD:
        kwargs["password"] = settings.MONGODB_PASSWORD
    if settings.MONGODB_DIRECT:
        kwargs["directConnection"] = True
    client = AsyncIOMotorClient(settings.MONGODB_URI, **kwargs)
    return client, client[settings.MONGODB_DATABASE]


# ═══════════════════════════════════════════
#  2. Skills 同步（从内置 manifest）
# ═══════════════════════════════════════════

SKILL_MANIFEST: list[dict] = [
    {"category": "ai-ops", "slug": "academic-writing", "name": "academic-writing", "description": "学术论文写作与原创性合规技能"},
    {"category": "ai-ops", "slug": "agent-briefing", "name": "agent-briefing", "description": "Agent 提示词生成 / 子 Agent 任务简报"},
    {"category": "ai-ops", "slug": "ai-content-marketing", "name": "ai-content-marketing", "description": "AI 内容营销实战排障版"},
    {"category": "ai-ops", "slug": "ai-engineering", "name": "ai-engineering", "description": "AI 工程实战排障版 - Claude/OpenAI/多模型接入"},
    {"category": "ai-ops", "slug": "ai-image-prompt", "name": "ai-image-prompt", "description": "AI 生图提示词实战排障版"},
    {"category": "ai-ops", "slug": "document-authoring", "name": "document-authoring", "description": "Word/docx 文档交付技能"},
    {"category": "ai-ops", "slug": "legal-counsel", "name": "legal-counsel", "description": "企业法务与法律合规技能"},
    {"category": "ai-ops", "slug": "llm-eval", "name": "llm-eval", "description": "LLM/agent/RAG/prompt/model 评测技能"},
    {"category": "ai-ops", "slug": "mcp-tool-use", "name": "mcp-tool-use", "description": "MCP 工具使用技能"},
    {"category": "ai-ops", "slug": "presentation-authoring", "name": "presentation-authoring", "description": "PPT/PPTX 演示稿交付技能"},
    {"category": "ai-ops", "slug": "product-manager", "name": "product-manager", "description": "产品经理技能实战排障版"},
    {"category": "ai-ops", "slug": "product-marketing", "name": "product-marketing", "description": "Product Marketing 实战排障版"},
    {"category": "ai-ops", "slug": "project-promo-writer", "name": "project-promo-writer", "description": "通用项目推广文案生成技能"},
    {"category": "ai-ops", "slug": "prompt-engineering", "name": "prompt-engineering", "description": "Prompt Engineering 实战技能"},
    {"category": "ai-ops", "slug": "research", "name": "research", "description": "Research 实战排障版"},
    {"category": "ai-ops", "slug": "social-media-ops", "name": "social-media-ops", "description": "社媒运营实战排障版"},
    {"category": "backend-api", "slug": "adyen", "name": "adyen", "description": "Adyen 支付实战技能"},
    {"category": "backend-api", "slug": "alipay-pay", "name": "alipay-pay", "description": "支付宝 Alipay 支付实战排障版"},
    {"category": "backend-api", "slug": "api-engineering", "name": "api-engineering", "description": "API 设计实战排障技能"},
    {"category": "backend-api", "slug": "apple-pay", "name": "apple-pay", "description": "Apple Pay / PassKit 支付实战技能"},
    {"category": "backend-api", "slug": "backend-engineering", "name": "backend-engineering", "description": "后端工程实战排障版技能"},
    {"category": "backend-api", "slug": "checkout-com", "name": "checkout-com", "description": "Checkout.com 支付实战排障版"},
    {"category": "backend-api", "slug": "google-pay", "name": "google-pay", "description": "Google Pay 实战排障版"},
    {"category": "backend-api", "slug": "graphql-grpc-events", "name": "graphql-grpc-events", "description": "GraphQL/gRPC/事件契约实战排障版"},
    {"category": "backend-api", "slug": "paypal", "name": "paypal", "description": "PayPal / Braintree 支付实战排障版"},
    {"category": "backend-api", "slug": "sdk-integration", "name": "sdk-integration", "description": "第三方 SDK 集成工程技能"},
    {"category": "backend-api", "slug": "square", "name": "square", "description": "Square 支付实战排障版"},
    {"category": "backend-api", "slug": "stripe", "name": "stripe", "description": "Stripe 支付实战排障版"},
    {"category": "backend-api", "slug": "wallet-engineering", "name": "wallet-engineering", "description": "站内余额/钱包账户工程技能"},
    {"category": "backend-api", "slug": "wallet-pass", "name": "wallet-pass", "description": "Apple Wallet / Google Wallet pass 票券技能"},
    {"category": "backend-api", "slug": "wechat-pay", "name": "wechat-pay", "description": "微信支付实战排障版"},
    {"category": "client-side", "slug": "alipay-miniprogram", "name": "alipay-miniprogram", "description": "支付宝小程序实战排障版"},
    {"category": "client-side", "slug": "android-development", "name": "android-development", "description": "Android 原生开发实战排障技能"},
    {"category": "client-side", "slug": "apple-development", "name": "apple-development", "description": "Apple 原生开发实战排障版"},
    {"category": "client-side", "slug": "autojs-automation", "name": "autojs-automation", "description": "AutoJS 自动化实战排障版"},
    {"category": "client-side", "slug": "douyin-miniprogram", "name": "douyin-miniprogram", "description": "抖音小程序原生开发实战排障版"},
    {"category": "client-side", "slug": "electron-development", "name": "electron-development", "description": "Electron 开发实战排障增强版"},
    {"category": "client-side", "slug": "embedded-firmware", "name": "embedded-firmware", "description": "Embedded Firmware 实战排障版"},
    {"category": "client-side", "slug": "flutter-development", "name": "flutter-development", "description": "Flutter 开发实战排障技能"},
    {"category": "client-side", "slug": "fpga-asic-hdl", "name": "fpga-asic-hdl", "description": "HDL/FPGA/ASIC 实战排障版"},
    {"category": "client-side", "slug": "harmonyos-arkts", "name": "harmonyos-arkts", "description": "HarmonyOS ArkTS 开发技能"},
    {"category": "client-side", "slug": "harmonyos-arkui", "name": "harmonyos-arkui", "description": "HarmonyOS ArkUI 开发技能"},
    {"category": "client-side", "slug": "linux-driver-development", "name": "linux-driver-development", "description": "Linux Driver Development 实战排障版"},
    {"category": "client-side", "slug": "tauri-development", "name": "tauri-development", "description": "Tauri 桌面与移动应用实战排障技能"},
    {"category": "client-side", "slug": "uefi-development", "name": "uefi-development", "description": "UEFI/EDK II 固件开发技能"},
    {"category": "client-side", "slug": "uniapp-development", "name": "uniapp-development", "description": "uni-app 跨端开发实战排障版"},
    {"category": "client-side", "slug": "wechat-miniprogram", "name": "wechat-miniprogram", "description": "微信小程序原生开发实战排障版"},
    {"category": "client-side", "slug": "windows-driver-development", "name": "windows-driver-development", "description": "Windows Driver Development 实战排障版"},
    {"category": "data-cloud", "slug": "amap-gaode", "name": "amap-gaode", "description": "高德地图全平台接入技能"},
    {"category": "data-cloud", "slug": "baidu-map", "name": "baidu-map", "description": "百度地图全平台接入技能"},
    {"category": "data-cloud", "slug": "cloud-native", "name": "cloud-native", "description": "Cloud Native 实战排障版"},
    {"category": "data-cloud", "slug": "data-engineering", "name": "data-engineering", "description": "数据工程实战排障版"},
    {"category": "data-cloud", "slug": "database-engineering", "name": "database-engineering", "description": "DB Design 实战排障技能"},
    {"category": "data-cloud", "slug": "esri-arcgis", "name": "esri-arcgis", "description": "Esri ArcGIS 开发技能"},
    {"category": "data-cloud", "slug": "finops", "name": "finops", "description": "FinOps 实战排障版"},
    {"category": "data-cloud", "slug": "google-maps-platform", "name": "google-maps-platform", "description": "Google Maps Platform 全平台开发技能"},
    {"category": "data-cloud", "slug": "huawei-map-kit", "name": "huawei-map-kit", "description": "华为 Map Kit 集成技能"},
    {"category": "data-cloud", "slug": "leaflet-openlayers", "name": "leaflet-openlayers", "description": "Leaflet/OpenLayers Web GIS 技能"},
    {"category": "data-cloud", "slug": "map-gis-core", "name": "map-gis-core", "description": "地图 GIS 核心开发技能"},
    {"category": "data-cloud", "slug": "mapbox-maplibre", "name": "mapbox-maplibre", "description": "Mapbox/MapLibre 地图渲染技能"},
    {"category": "data-cloud", "slug": "openstreetmap-routing", "name": "openstreetmap-routing", "description": "OSM 地理编码与路由技能"},
    {"category": "data-cloud", "slug": "perf-engineering", "name": "perf-engineering", "description": "性能工程技能实战排障版"},
    {"category": "data-cloud", "slug": "platform-engineering", "name": "platform-engineering", "description": "平台工程技能实战排障版"},
    {"category": "data-cloud", "slug": "spreadsheet-analysis", "name": "spreadsheet-analysis", "description": "Excel/xlsx/csv 表格分析技能"},
    {"category": "data-cloud", "slug": "tencent-map", "name": "tencent-map", "description": "腾讯位置服务全平台开发技能"},
    {"category": "data-cloud", "slug": "terraform-iac", "name": "terraform-iac", "description": "Terraform/IaC 实战排障版"},
    {"category": "data-cloud", "slug": "tianditu-map", "name": "tianditu-map", "description": "天地图开发技能"},
    {"category": "design", "slug": "brand-visual-direction", "name": "brand-visual-direction", "description": "设计方向总监技能"},
    {"category": "design", "slug": "design-audit", "name": "design-audit", "description": "证据化设计审计技能"},
    {"category": "design", "slug": "design-brief", "name": "design-brief", "description": "设计简报塑形技能"},
    {"category": "design", "slug": "design-system", "name": "design-system", "description": "设计系统构建技能"},
    {"category": "design", "slug": "icon-design", "name": "icon-design", "description": "图标设计实战排障版"},
    {"category": "design", "slug": "react-development", "name": "react-development", "description": "React/Next.js 开发技能"},
    {"category": "design", "slug": "screenshot-to-ui", "name": "screenshot-to-ui", "description": "Image to UI 实战排障版"},
    {"category": "design", "slug": "ui-architecture", "name": "ui-architecture", "description": "UI 信息架构技能"},
    {"category": "design", "slug": "ui-design", "name": "ui-design", "description": "UI Design 视觉落地技能"},
    {"category": "design", "slug": "vue-development", "name": "vue-development", "description": "Vue 开发实战排障版"},
    {"category": "languages", "slug": "cpp-development", "name": "cpp-development", "description": "C++ Dev 实战排障版"},
    {"category": "languages", "slug": "dotnet-development", "name": "dotnet-development", "description": ".NET Dev 实战排障版"},
    {"category": "languages", "slug": "elixir-erlang-development", "name": "elixir-erlang-development", "description": "Elixir/Erlang OTP 实战排障版"},
    {"category": "languages", "slug": "go-development", "name": "go-development", "description": "Go 规范基线与排障技能"},
    {"category": "languages", "slug": "java-jvm-development", "name": "java-jvm-development", "description": "Java/JVM 实战排障技能"},
    {"category": "languages", "slug": "javascript-typescript-development", "name": "javascript-typescript-development", "description": "JavaScript/TypeScript 开发技能"},
    {"category": "languages", "slug": "kotlin-development", "name": "kotlin-development", "description": "Kotlin 资深开发技能"},
    {"category": "languages", "slug": "lua-openresty-development", "name": "lua-openresty-development", "description": "Lua 开发与自动化技能"},
    {"category": "languages", "slug": "php-development", "name": "php-development", "description": "PHP 开发技能实战排障版"},
    {"category": "languages", "slug": "python-development", "name": "python-development", "description": "Python Dev 实战排障版"},
    {"category": "languages", "slug": "r-development", "name": "r-development", "description": "R 语言开发闭环技能"},
    {"category": "languages", "slug": "ruby-development", "name": "ruby-development", "description": "Ruby 开发技能"},
    {"category": "languages", "slug": "rust-development", "name": "rust-development", "description": "Rust Dev 实战排障版"},
    {"category": "languages", "slug": "scala-development", "name": "scala-development", "description": "Scala Dev 实战排障版"},
    {"category": "languages", "slug": "shell-scripting", "name": "shell-scripting", "description": "Shell 脚本实战排障版"},
    {"category": "languages", "slug": "typescript-development", "name": "typescript-development", "description": "TypeScript 实战开发技能"},
    {"category": "security", "slug": "devsecops", "name": "devsecops", "description": "DevSecOps 实战排障版"},
    {"category": "security", "slug": "mobile-security", "name": "mobile-security", "description": "移动安全与渗透测试技能"},
    {"category": "security", "slug": "protocol-analysis", "name": "protocol-analysis", "description": "Protocol Analysis 实战排障版"},
    {"category": "security", "slug": "reverse-engineering", "name": "reverse-engineering", "description": "逆向工程实战排障版"},
    {"category": "security", "slug": "web-security", "name": "web-security", "description": "Web Security 实战排障版"},
    {"category": "test-release", "slug": "browser-automation", "name": "browser-automation", "description": "浏览器自动化工程技能"},
    {"category": "test-release", "slug": "code-audit", "name": "code-audit", "description": "代码审计实战排障版"},
    {"category": "test-release", "slug": "git-workflow", "name": "git-workflow", "description": "Git 工作流实战排障版"},
    {"category": "test-release", "slug": "observability", "name": "observability", "description": "可观测性与 SRE 技能"},
    {"category": "test-release", "slug": "project-learning", "name": "project-learning", "description": "项目学习实战排障版"},
    {"category": "test-release", "slug": "release-engineering", "name": "release-engineering", "description": "发布工程实战排障版"},
    {"category": "test-release", "slug": "test-engineering", "name": "test-engineering", "description": "Test Engineering 实战排障版"},
    {"category": "coordination", "slug": "controller", "name": "controller", "description": "BaJie-MCP 主控中心调度协议"},
]

CATEGORY_META: dict[str, dict] = {
    "ai-ops": {"name": "AI 运营", "description": "AI 工程、Prompt、评测、内容营销、产品、社媒、文档", "sort_order": 1},
    "backend-api": {"name": "后端 API", "description": "后端工程、API 设计、支付集成、SDK 集成", "sort_order": 2},
    "client-side": {"name": "端侧开发", "description": "Android/iOS/Flutter/Electron/小程序/嵌入式/驱动", "sort_order": 3},
    "data-cloud": {"name": "数据与云", "description": "数据工程、数据库、云原生、地图GIS、性能、平台、IaC", "sort_order": 4},
    "design": {"name": "设计", "description": "UI设计、设计系统、品牌视觉、React/Vue前端、图标", "sort_order": 5},
    "languages": {"name": "编程语言", "description": "Go/Python/Java/Rust/C++/.NET/TS/PHP/Ruby/Scala/Elixir/Lua/R/Shell", "sort_order": 6},
    "security": {"name": "安全", "description": "DevSecOps、Web安全、移动安全、协议分析、逆向工程", "sort_order": 7},
    "test-release": {"name": "测试与发布", "description": "代码审计、Git工作流、测试验证、性能工程、浏览器自动化、可观测性、发布工程", "sort_order": 8},
    "coordination": {"name": "协调", "description": "主控调度、群组协调", "sort_order": 9},
}


async def sync_skills(db, *, overwrite: bool = False, prune_stale: bool = False):
    from api.dao import skills as skills_dao
    from api.db.collections import SKILLS_COLLECTION, SKILL_CATEGORIES_COLLECTION

    await skills_dao.ensure_indexes(db)

    skill_files = sorted(SKILLS_LIBRARY_DIR.glob("*/SKILL.md"))
    categories: dict[str, dict[str, object]] = {}
    parsed_skills: list[tuple[str, dict[str, object], str]] = []

    for path in skill_files:
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        slug = path.parent.name
        category = str(meta.get("category") or "general")
        parsed_skills.append((slug, meta, body))
        categories.setdefault(
            category,
            {
                "name": category.replace("_", " ").replace("-", " ").title(),
                "description": f"Sere1nGraph skill category: {category}",
                "sort_order": len(categories) + 1,
            },
        )

    # Keep legacy category names for known categories, but only sync categories
    # that actually exist in Sere1nGraph/graph/skills/library.
    for slug, meta in CATEGORY_META.items():
        if slug in categories:
            categories[slug].update(meta)

    cat_count = 0
    for slug, meta in categories.items():
        await skills_dao.upsert_category_by_slug(db, slug, {
            "name": meta["name"],
            "description": meta["description"],
            "sort_order": int(meta["sort_order"]),
        })
        cat_count += 1
    print(f"[Skills] 分类同步: {cat_count} 个")

    all_tags: set[str] = set()
    skill_count = 0
    skip_count = 0
    for slug, meta, body in parsed_skills:
        reference_contents = _load_skill_references(slug)
        if not overwrite:
            existing = await skills_dao.get_skill_by_slug(db, slug)
            if existing:
                existing_meta = existing.get("meta")
                existing_meta = existing_meta if isinstance(existing_meta, dict) else {}
                if reference_contents and not existing_meta.get("reference_contents"):
                    await skills_dao.update_skill(
                        db,
                        existing["skill_id"],
                        meta={**existing_meta, "reference_contents": reference_contents},
                    )
                skip_count += 1
                continue

        category = str(meta.get("category") or "general")
        tags = sorted(set([category] + _as_list(meta.get("tags"))))
        all_tags.update(tags)

        await skills_dao.upsert_skill_by_slug(db, slug, {
            "name": str(meta.get("name") or slug),
            "category": category,
            "description": str(meta.get("description") or ""),
            "content_raw": body.strip(),
            "tags": tags,
            "triggers": _as_list(meta.get("triggers")),
            "anti_triggers": _as_list(meta.get("anti_triggers")),
            "aliases": _as_list(meta.get("aliases")),
            "requires": _as_list(meta.get("requires")),
            "related": _as_list(meta.get("related")),
            "file_signals": _as_list(meta.get("file_signals")),
            "risk_signals": _as_list(meta.get("risk_signals")),
            "priority": int(meta.get("priority") or 0),
            "meta": {
                "source": "Sere1nGraph/graph/skills/library",
                "source_path": str((SKILLS_LIBRARY_DIR / slug / "SKILL.md").relative_to(ROOT)),
                "phases": _as_list(meta.get("phases")),
                "reference_contents": reference_contents,
            },
            "status": "approved",
            "created_by": "system",
        })
        skill_count += 1

    new_tags = await skills_dao.bulk_upsert_tags(db, list(all_tags))
    print(f"[Skills] 技能同步: {skill_count} 写入, {skip_count} 跳过")
    print(f"[Skills] 标签同步: {new_tags} 新增")

    if prune_stale:
        local_slugs = [slug for slug, _, _ in parsed_skills]
        stale_query = {
            "slug": {"$nin": local_slugs},
            "$or": [
                {"created_by": "system"},
                {"meta.source": {"$exists": False}},
                {"meta.source": None},
            ],
        }
        deleted = await db[SKILLS_COLLECTION].delete_many(stale_query)

        used_categories = {
            doc["category"]
            async for doc in db[SKILLS_COLLECTION].find(
                {"category": {"$exists": True}},
                {"_id": 0, "category": 1},
            )
            if doc.get("category")
        }
        stale_categories = 0
        async for cat in db[SKILL_CATEGORIES_COLLECTION].find(
            {"slug": {"$nin": list(used_categories)}},
            {"_id": 0, "category_id": 1, "slug": 1},
        ):
            if await db[SKILLS_COLLECTION].count_documents({"category": cat.get("slug")}) == 0:
                result = await db[SKILL_CATEGORIES_COLLECTION].delete_one(
                    {"category_id": cat["category_id"]}
                )
                stale_categories += result.deleted_count

        print(f"[Skills] 旧系统种子清理: {deleted.deleted_count} 个 skill, {stale_categories} 个空分类")


# ═══════════════════════════════════════════
#  3. Prompts 同步（种子分类 + 示例数据）
# ═══════════════════════════════════════════

PROMPT_CATEGORIES: list[dict] = [
    {"slug": "system-prompts", "name": "系统提示词", "description": "Agent/LLM 系统级 system prompt", "sort_order": 1},
    {"slug": "task-templates", "name": "任务模板", "description": "常用任务的 prompt 模板", "sort_order": 2},
    {"slug": "analysis", "name": "分析类", "description": "数据分析、代码分析、安全分析等 prompt", "sort_order": 3},
    {"slug": "generation", "name": "生成类", "description": "文案、代码、报告等内容生成 prompt", "sort_order": 4},
    {"slug": "review", "name": "审查类", "description": "代码审查、文档审查、设计审查 prompt", "sort_order": 5},
    {"slug": "extraction", "name": "提取类", "description": "信息提取、实体识别、关键词提取 prompt", "sort_order": 6},
    {"slug": "conversation", "name": "对话类", "description": "客服、咨询、问答等对话型 prompt", "sort_order": 7},
    {"slug": "custom", "name": "自定义", "description": "用户自定义 prompt", "sort_order": 99},
]

PROMPT_SEEDS: list[dict] = [
    {
        "slug": "code-review-general",
        "name": "通用代码审查",
        "category": "review",
        "description": "通用代码审查提示词，检查代码质量、安全性和最佳实践",
        "system_prompt": "你是一名资深代码审查员。请仔细审查提交的代码变更。",
        "user_prompt_template": "请审查以下代码变更：\n\n```{language}\n{code}\n```\n\n重点关注：安全性、性能、可维护性、错误处理。",
        "variables": ["language", "code"],
        "tags": ["code-review", "quality"],
    },
    {
        "slug": "api-doc-generator",
        "name": "API 文档生成",
        "category": "generation",
        "description": "根据代码自动生成 API 接口文档",
        "system_prompt": "你是一名技术文档专家。请根据给定的 API 代码生成清晰、完整的接口文档。",
        "user_prompt_template": "请为以下 API 端点生成文档：\n\n```{language}\n{code}\n```\n\n输出格式：Markdown，包含 URL、方法、参数、请求示例、响应示例。",
        "variables": ["language", "code"],
        "tags": ["documentation", "api"],
    },
    {
        "slug": "security-scan-report",
        "name": "安全扫描报告",
        "category": "analysis",
        "description": "安全扫描结果分析与报告生成",
        "system_prompt": "你是一名安全分析师。请分析安全扫描结果，给出风险评估和修复建议。",
        "user_prompt_template": "以下是安全扫描结果：\n\n{scan_results}\n\n请按严重程度分类，给出每个问题的风险评估和修复方案。",
        "variables": ["scan_results"],
        "tags": ["security", "analysis"],
    },
]


async def sync_prompts(db, *, overwrite: bool = False):
    from api.dao import prompts as prompts_dao

    await prompts_dao.ensure_indexes(db)

    prompt_files = sorted(
        p for p in PROMPTS_DIR.rglob("*.md") if "__pycache__" not in p.parts
    )
    categories: dict[str, dict[str, object]] = {}
    parsed_prompts: list[tuple[str, str, Path, str]] = []

    for path in prompt_files:
        rel = path.relative_to(PROMPTS_DIR)
        category = rel.parent.as_posix() if rel.parent != Path(".") else "root"
        slug = rel.with_suffix("").as_posix()
        parsed_prompts.append((slug, category, path, path.read_text(encoding="utf-8")))
        categories.setdefault(
            category,
            {
                "name": category.replace("_", " ").replace("-", " ").title(),
                "description": f"Sere1nGraph prompt category: {category}",
                "sort_order": len(categories) + 1,
            },
        )

    legacy_categories = {cat["slug"]: cat for cat in PROMPT_CATEGORIES}
    for slug, cat in legacy_categories.items():
        if slug in categories:
            categories[slug].update(cat)

    cat_count = 0
    for slug, cat in categories.items():
        await prompts_dao.upsert_category_by_slug(db, slug, {
            "name": cat["name"],
            "description": cat["description"],
            "sort_order": int(cat["sort_order"]),
        })
        cat_count += 1
    print(f"[Prompts] 分类同步: {cat_count} 个")

    all_tags: set[str] = set()
    prompt_count = 0
    skip_count = 0
    for slug, category, path, content in parsed_prompts:
        if not overwrite:
            existing = await prompts_dao.get_prompt_by_slug(db, slug)
            if existing:
                skip_count += 1
                continue

        tags = [category]
        all_tags.update(tags)

        await prompts_dao.upsert_prompt_by_slug(db, slug, {
            "name": path.stem.replace("_", " ").replace("-", " ").title(),
            "category": category,
            "description": f"Synced from {path.relative_to(ROOT)}",
            "content": content,
            "system_prompt": content,
            "user_prompt_template": "",
            "variables": [],
            "tags": tags,
            "meta": {
                "source": "Sere1nGraph/graph/prompts",
                "source_path": str(path.relative_to(ROOT)),
            },
            "status": "approved",
            "created_by": "system",
        })
        prompt_count += 1

    new_tags = await prompts_dao.bulk_upsert_tags(db, list(all_tags))
    print(f"[Prompts] 提示词同步: {prompt_count} 写入, {skip_count} 跳过")
    print(f"[Prompts] 标签同步: {new_tags} 新增")


# ═══════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="同步 skills/prompts 到 MongoDB")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有数据")
    parser.add_argument("--prune-stale", action="store_true", help="清理不在本地目录中的旧系统种子 skills")
    parser.add_argument("--only", choices=["skills", "prompts"], help="仅同步指定模块")
    args = parser.parse_args()

    client, db = _get_db()

    print("=" * 50)
    print("  Sere1nFish 数据同步脚本")
    print("=" * 50)

    try:
        targets = [args.only] if args.only else ["skills", "prompts"]

        if "skills" in targets:
            print("\n--- Skills 同步 ---")
            await sync_skills(db, overwrite=args.overwrite, prune_stale=args.prune_stale)

        if "prompts" in targets:
            print("\n--- Prompts 同步 ---")
            await sync_prompts(db, overwrite=args.overwrite)

        print("\n✅ 同步完成！")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
