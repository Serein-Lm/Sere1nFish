"""
Skills 技能库 API

完整 CRUD + 分类管理 + 标签管理 + 审核流 + 分场景查询
写操作需 admin 权限；读操作需登录
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from api.auth import get_current_active_user, require_admin, User
from api.db.mongodb import get_db
from api.dao import skills as skills_dao

router = APIRouter()
logger = logging.getLogger(__name__)


async def _refresh_runtime(db) -> None:
    try:
        from api.services.library_runtime import refresh_skill_runtime

        await refresh_skill_runtime(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("刷新运行时 Skill registry 失败: %s", exc)


# ═══════════════════════════════════════════
#  Pydantic 请求/响应模型
# ═══════════════════════════════════════════

class CategoryCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    parent_id: str | None = None
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    parent_id: str | None = None
    sort_order: int | None = None


class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    color: str = ""
    description: str = ""


class TagUpdate(BaseModel):
    name: str | None = None
    color: str | None = None
    description: str | None = None


class SkillCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=256)
    category: str = Field(..., min_length=1)
    description: str = ""
    content_raw: str = ""
    tags: list[str] = []
    triggers: list[str] = []
    anti_triggers: list[str] = []
    aliases: list[str] = []
    requires: list[str] = []
    related: list[str] = []
    file_signals: list[str] = []
    risk_signals: list[str] = []
    priority: int = 0
    meta: dict[str, Any] = {}


class SkillUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    content_raw: str | None = None
    tags: list[str] | None = None
    triggers: list[str] | None = None
    anti_triggers: list[str] | None = None
    aliases: list[str] | None = None
    requires: list[str] | None = None
    related: list[str] | None = None
    file_signals: list[str] | None = None
    risk_signals: list[str] | None = None
    priority: int | None = None
    meta: dict[str, Any] | None = None


class ReviewRequest(BaseModel):
    approved: bool
    comment: str = ""


# ═══════════════════════════════════════════
#  分类接口
# ═══════════════════════════════════════════

@router.get("/categories")
async def list_categories(
    _: User = Depends(get_current_active_user),
):
    db = get_db()
    cats = await skills_dao.list_categories(db)
    return {"items": cats}


@router.get("/categories/tree")
async def get_category_tree(
    _: User = Depends(get_current_active_user),
):
    db = get_db()
    tree = await skills_dao.get_category_tree(db)
    return {"tree": tree}


@router.get("/categories/{category_id}")
async def get_category(
    category_id: str,
    _: User = Depends(get_current_active_user),
):
    db = get_db()
    cat = await skills_dao.get_category(db, category_id)
    if not cat:
        raise HTTPException(404, "分类不存在")
    return cat


@router.post("/categories", status_code=201)
async def create_category(
    body: CategoryCreate,
    _: User = Depends(require_admin),
):
    db = get_db()
    existing = await skills_dao.get_category_by_slug(db, body.slug)
    if existing:
        raise HTTPException(409, f"分类 slug '{body.slug}' 已存在")
    cat = await skills_dao.create_category(
        db,
        slug=body.slug,
        name=body.name,
        description=body.description,
        parent_id=body.parent_id,
        sort_order=body.sort_order,
    )
    return cat


@router.put("/categories/{category_id}")
async def update_category(
    category_id: str,
    body: CategoryUpdate,
    _: User = Depends(require_admin),
):
    db = get_db()
    cat = await skills_dao.update_category(
        db, category_id, **body.model_dump(exclude_none=True)
    )
    if not cat:
        raise HTTPException(404, "分类不存在")
    return cat


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: str,
    _: User = Depends(require_admin),
):
    db = get_db()
    ok = await skills_dao.delete_category(db, category_id)
    if not ok:
        raise HTTPException(404, "分类不存在")
    return {"ok": True}


# ═══════════════════════════════════════════
#  标签接口
# ═══════════════════════════════════════════

@router.get("/tags")
async def list_tags(_: User = Depends(get_current_active_user)):
    db = get_db()
    tags = await skills_dao.list_tags(db)
    return {"items": tags}


@router.post("/tags", status_code=201)
async def create_tag(
    body: TagCreate,
    _: User = Depends(require_admin),
):
    db = get_db()
    existing = await skills_dao.get_tag_by_name(db, body.name)
    if existing:
        raise HTTPException(409, f"标签 '{body.name}' 已存在")
    tag = await skills_dao.create_tag(
        db, name=body.name, color=body.color, description=body.description
    )
    return tag


@router.put("/tags/{tag_id}")
async def update_tag(
    tag_id: str,
    body: TagUpdate,
    _: User = Depends(require_admin),
):
    db = get_db()
    tag = await skills_dao.update_tag(db, tag_id, **body.model_dump(exclude_none=True))
    if not tag:
        raise HTTPException(404, "标签不存在")
    return tag


@router.delete("/tags/{tag_id}")
async def delete_tag(
    tag_id: str,
    _: User = Depends(require_admin),
):
    db = get_db()
    ok = await skills_dao.delete_tag(db, tag_id)
    if not ok:
        raise HTTPException(404, "标签不存在")
    return {"ok": True}


# ═══════════════════════════════════════════
#  Skill 主体接口
# ═══════════════════════════════════════════

@router.get("")
async def list_skills(
    category: str | None = Query(None, description="按分类筛选"),
    tag: str | None = Query(None, description="按标签筛选"),
    status: str | None = Query(None, description="按状态筛选"),
    search: str | None = Query(None, description="全文搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("updated_at"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    include_content: bool = Query(False, description="是否包含 content_raw"),
    _: User = Depends(get_current_active_user),
):
    db = get_db()
    from pymongo import ASCENDING, DESCENDING
    order = ASCENDING if sort_order == "asc" else DESCENDING
    result = await skills_dao.list_skills(
        db,
        category=category,
        tag=tag,
        status=status,
        search=search,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=order,
        include_content=include_content,
    )
    return result


@router.get("/grouped")
async def list_skills_grouped(
    status: str | None = Query("approved", description="按状态筛选"),
    _: User = Depends(get_current_active_user),
):
    """按分类分组返回（场景化披露用）"""
    db = get_db()
    grouped = await skills_dao.list_skills_by_category(db, status=status)
    return {"groups": grouped}


@router.get("/stats")
async def get_stats(_: User = Depends(get_current_active_user)):
    db = get_db()
    counts = await skills_dao.count_by_status(db)
    return {"status_counts": counts}


@router.post("/sync/from-files")
async def sync_from_files(
    overwrite: bool = Query(False, description="是否覆盖已有同 slug 数据"),
    prune_stale: bool = Query(False, description="是否清理不在本地 skills/library 中的旧系统种子"),
    _: User = Depends(require_admin),
):
    """从 Sere1nGraph/graph/skills/library 同步技能文件到数据库。"""
    db = get_db()
    from scripts.sync_to_db import sync_skills

    await sync_skills(db, overwrite=overwrite, prune_stale=prune_stale)
    await _refresh_runtime(db)
    result = await skills_dao.list_skills(db, page=1, page_size=1)
    return {
        "ok": True,
        "total": result["total"],
        "overwrite": overwrite,
        "prune_stale": prune_stale,
    }


@router.get("/detail/{skill_id}")
async def get_skill(
    skill_id: str,
    _: User = Depends(get_current_active_user),
):
    db = get_db()
    skill = await skills_dao.get_skill(db, skill_id)
    if not skill:
        skill = await skills_dao.get_skill_by_slug(db, skill_id)
    if not skill:
        raise HTTPException(404, "Skill 不存在")
    return skill


@router.post("", status_code=201)
async def create_skill(
    body: SkillCreate,
    user: User = Depends(get_current_active_user),
):
    db = get_db()
    existing = await skills_dao.get_skill_by_slug(db, body.slug)
    if existing:
        raise HTTPException(409, f"Skill slug '{body.slug}' 已存在")

    initial_status = "approved" if user.is_admin else "pending_review"

    skill = await skills_dao.create_skill(
        db,
        slug=body.slug,
        name=body.name,
        category=body.category,
        description=body.description,
        content_raw=body.content_raw,
        tags=body.tags,
        triggers=body.triggers,
        anti_triggers=body.anti_triggers,
        aliases=body.aliases,
        requires=body.requires,
        related=body.related,
        file_signals=body.file_signals,
        risk_signals=body.risk_signals,
        priority=body.priority,
        meta=body.meta,
        status=initial_status,
        created_by=user.username,
    )

    if body.tags:
        await skills_dao.bulk_upsert_tags(db, body.tags)

    await _refresh_runtime(db)
    return skill


@router.put("/detail/{skill_id}")
async def update_skill(
    skill_id: str,
    body: SkillUpdate,
    user: User = Depends(get_current_active_user),
):
    db = get_db()
    existing = await skills_dao.get_skill(db, skill_id)
    if not existing:
        raise HTTPException(404, "Skill 不存在")

    if not user.is_admin and existing.get("created_by") != user.username:
        raise HTTPException(403, "只能编辑自己创建的 Skill")

    fields = body.model_dump(exclude_none=True)
    if not user.is_admin and existing.get("status") == "approved":
        fields["status"] = "pending_review"

    skill = await skills_dao.update_skill(db, skill_id, **fields)

    if body.tags:
        await skills_dao.bulk_upsert_tags(db, body.tags)

    await _refresh_runtime(db)
    return skill


@router.delete("/detail/{skill_id}")
async def delete_skill(
    skill_id: str,
    _: User = Depends(require_admin),
):
    db = get_db()
    ok = await skills_dao.delete_skill(db, skill_id)
    if not ok:
        raise HTTPException(404, "Skill 不存在")
    await _refresh_runtime(db)
    return {"ok": True}


# ═══════════════════════════════════════════
#  审核接口（仅 admin）
# ═══════════════════════════════════════════

@router.post("/detail/{skill_id}/submit-review")
async def submit_for_review(
    skill_id: str,
    user: User = Depends(get_current_active_user),
):
    db = get_db()
    skill = await skills_dao.submit_for_review(db, skill_id)
    if not skill:
        raise HTTPException(400, "只有 draft 或 rejected 状态的 Skill 可以提交审核")
    await _refresh_runtime(db)
    return skill


@router.get("/review/pending")
async def list_pending_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_admin),
):
    db = get_db()
    return await skills_dao.list_skills(
        db,
        status="pending_review",
        page=page,
        page_size=page_size,
        include_content=True,
    )


@router.post("/detail/{skill_id}/review")
async def review_skill(
    skill_id: str,
    body: ReviewRequest,
    user: User = Depends(require_admin),
):
    db = get_db()
    skill = await skills_dao.review_skill(
        db,
        skill_id,
        approved=body.approved,
        reviewer=user.username,
        comment=body.comment,
    )
    if not skill:
        raise HTTPException(400, "只能审核 pending_review 状态的 Skill")
    await _refresh_runtime(db)
    return skill


@router.post("/detail/{skill_id}/archive")
async def archive_skill(
    skill_id: str,
    _: User = Depends(require_admin),
):
    db = get_db()
    skill = await skills_dao.archive_skill(db, skill_id)
    if not skill:
        raise HTTPException(404, "Skill 不存在")
    await _refresh_runtime(db)
    return skill


# ═══════════════════════════════════════════
#  批量操作 & 导出（admin）
# ═══════════════════════════════════════════

class BatchDeleteRequest(BaseModel):
    skill_ids: list[str] = Field(..., min_length=1, max_length=100)


class BatchReviewRequest(BaseModel):
    skill_ids: list[str] = Field(..., min_length=1, max_length=100)
    approved: bool
    comment: str = ""


class BatchTagRequest(BaseModel):
    skill_ids: list[str] = Field(..., min_length=1, max_length=100)
    add_tags: list[str] = []
    remove_tags: list[str] = []


@router.post("/batch/delete")
async def batch_delete(
    body: BatchDeleteRequest,
    _: User = Depends(require_admin),
):
    db = get_db()
    deleted = 0
    for sid in body.skill_ids:
        if await skills_dao.delete_skill(db, sid):
            deleted += 1
    await _refresh_runtime(db)
    return {"deleted": deleted, "total": len(body.skill_ids)}


@router.post("/batch/review")
async def batch_review(
    body: BatchReviewRequest,
    user: User = Depends(require_admin),
):
    db = get_db()
    reviewed = 0
    for sid in body.skill_ids:
        r = await skills_dao.review_skill(
            db, sid, approved=body.approved, reviewer=user.username, comment=body.comment
        )
        if r:
            reviewed += 1
    await _refresh_runtime(db)
    return {"reviewed": reviewed, "total": len(body.skill_ids)}


@router.post("/batch/tags")
async def batch_update_tags(
    body: BatchTagRequest,
    _: User = Depends(require_admin),
):
    db = get_db()
    from api.db.collections import SKILLS_COLLECTION
    updated = 0
    for sid in body.skill_ids:
        ops = {}
        if body.add_tags:
            ops["$addToSet"] = {"tags": {"$each": body.add_tags}}
        if body.remove_tags:
            ops["$pull"] = {"tags": {"$in": body.remove_tags}} if len(body.remove_tags) > 1 else {"tags": body.remove_tags[0]}
        if ops:
            r = await db[SKILLS_COLLECTION].update_one({"skill_id": sid}, ops)
            if r.modified_count:
                updated += 1
    if body.add_tags:
        await skills_dao.bulk_upsert_tags(db, body.add_tags)
    await _refresh_runtime(db)
    return {"updated": updated, "total": len(body.skill_ids)}


@router.get("/export/all")
async def export_all(
    _: User = Depends(require_admin),
):
    """导出全部 Skills 数据（含分类、标签、skill 全文）"""
    db = get_db()
    categories = await skills_dao.list_categories(db)
    tags = await skills_dao.list_tags(db)
    result = await skills_dao.list_skills(db, page_size=10000, include_content=True)
    return {
        "categories": categories,
        "tags": tags,
        "skills": result["items"],
        "total": result["total"],
    }
