"""
Prompts 提示词库 API

完整 CRUD + 分类管理 + 标签管理 + 审核流
写操作需 admin 权限；读操作需登录
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from api.auth import get_current_active_user, require_admin, User
from api.db.mongodb import get_db
from api.dao import prompts as prompts_dao

router = APIRouter()
logger = logging.getLogger(__name__)


async def _refresh_runtime(db) -> None:
    try:
        from api.services.library_runtime import refresh_prompt_runtime

        await refresh_prompt_runtime(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("刷新运行时 Prompt cache 失败: %s", exc)


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


class PromptCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=256)
    category: str = Field(..., min_length=1)
    description: str = ""
    content: str = ""
    system_prompt: str = ""
    user_prompt_template: str = ""
    variables: list[str] = []
    tags: list[str] = []
    model_hint: str = ""
    temperature: float | None = None
    max_tokens: int | None = None
    meta: dict[str, Any] = {}


class PromptUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    content: str | None = None
    system_prompt: str | None = None
    user_prompt_template: str | None = None
    variables: list[str] | None = None
    tags: list[str] | None = None
    model_hint: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    meta: dict[str, Any] | None = None


class ReviewRequest(BaseModel):
    approved: bool
    comment: str = ""


# ═══════════════════════════════════════════
#  分类接口
# ═══════════════════════════════════════════

@router.get("/categories")
async def list_categories(_: User = Depends(get_current_active_user)):
    db = get_db()
    cats = await prompts_dao.list_categories(db)
    return {"items": cats}


@router.get("/categories/tree")
async def get_category_tree(_: User = Depends(get_current_active_user)):
    db = get_db()
    tree = await prompts_dao.get_category_tree(db)
    return {"tree": tree}


@router.get("/categories/{category_id}")
async def get_category(
    category_id: str,
    _: User = Depends(get_current_active_user),
):
    db = get_db()
    cat = await prompts_dao.get_category(db, category_id)
    if not cat:
        raise HTTPException(404, "分类不存在")
    return cat


@router.post("/categories", status_code=201)
async def create_category(
    body: CategoryCreate,
    _: User = Depends(require_admin),
):
    db = get_db()
    existing = await prompts_dao.get_category_by_slug(db, body.slug)
    if existing:
        raise HTTPException(409, f"分类 slug '{body.slug}' 已存在")
    cat = await prompts_dao.create_category(
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
    cat = await prompts_dao.update_category(
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
    ok = await prompts_dao.delete_category(db, category_id)
    if not ok:
        raise HTTPException(404, "分类不存在")
    return {"ok": True}


# ═══════════════════════════════════════════
#  标签接口
# ═══════════════════════════════════════════

@router.get("/tags")
async def list_tags(_: User = Depends(get_current_active_user)):
    db = get_db()
    tags = await prompts_dao.list_tags(db)
    return {"items": tags}


@router.post("/tags", status_code=201)
async def create_tag(
    body: TagCreate,
    _: User = Depends(require_admin),
):
    db = get_db()
    existing = await prompts_dao.get_tag_by_name(db, body.name)
    if existing:
        raise HTTPException(409, f"标签 '{body.name}' 已存在")
    tag = await prompts_dao.create_tag(
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
    tag = await prompts_dao.update_tag(db, tag_id, **body.model_dump(exclude_none=True))
    if not tag:
        raise HTTPException(404, "标签不存在")
    return tag


@router.delete("/tags/{tag_id}")
async def delete_tag(
    tag_id: str,
    _: User = Depends(require_admin),
):
    db = get_db()
    ok = await prompts_dao.delete_tag(db, tag_id)
    if not ok:
        raise HTTPException(404, "标签不存在")
    return {"ok": True}


# ═══════════════════════════════════════════
#  Prompt 主体接口
# ═══════════════════════════════════════════

@router.get("")
async def list_prompts(
    category: str | None = Query(None, description="按分类筛选"),
    tag: str | None = Query(None, description="按标签筛选"),
    status: str | None = Query(None, description="按状态筛选"),
    search: str | None = Query(None, description="全文搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("updated_at"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    _: User = Depends(get_current_active_user),
):
    db = get_db()
    from pymongo import ASCENDING, DESCENDING
    order = ASCENDING if sort_order == "asc" else DESCENDING
    result = await prompts_dao.list_prompts(
        db,
        category=category,
        tag=tag,
        status=status,
        search=search,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=order,
    )
    return result


@router.get("/stats")
async def get_stats(_: User = Depends(get_current_active_user)):
    db = get_db()
    counts = await prompts_dao.count_by_status(db)
    return {"status_counts": counts}


@router.post("/sync/from-files")
async def sync_from_files(
    overwrite: bool = Query(False, description="是否覆盖已有同 slug 数据"),
    _: User = Depends(require_admin),
):
    """从 Sere1nGraph/graph/prompts 同步提示词文件到数据库。"""
    db = get_db()
    from scripts.sync_to_db import sync_prompts

    await sync_prompts(db, overwrite=overwrite)
    await _refresh_runtime(db)
    result = await prompts_dao.list_prompts(db, page=1, page_size=1)
    return {"ok": True, "total": result["total"], "overwrite": overwrite}


@router.get("/detail/{prompt_id}")
async def get_prompt(
    prompt_id: str,
    _: User = Depends(get_current_active_user),
):
    db = get_db()
    prompt = await prompts_dao.get_prompt(db, prompt_id)
    if not prompt:
        prompt = await prompts_dao.get_prompt_by_slug(db, prompt_id)
    if not prompt:
        raise HTTPException(404, "Prompt 不存在")
    return prompt


@router.post("", status_code=201)
async def create_prompt(
    body: PromptCreate,
    user: User = Depends(get_current_active_user),
):
    db = get_db()
    existing = await prompts_dao.get_prompt_by_slug(db, body.slug)
    if existing:
        raise HTTPException(409, f"Prompt slug '{body.slug}' 已存在")

    initial_status = "approved" if user.is_admin else "pending_review"

    prompt = await prompts_dao.create_prompt(
        db,
        slug=body.slug,
        name=body.name,
        category=body.category,
        description=body.description,
        content=body.content,
        system_prompt=body.system_prompt,
        user_prompt_template=body.user_prompt_template,
        variables=body.variables,
        tags=body.tags,
        model_hint=body.model_hint,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        meta=body.meta,
        status=initial_status,
        created_by=user.username,
    )

    if body.tags:
        await prompts_dao.bulk_upsert_tags(db, body.tags)

    await _refresh_runtime(db)
    return prompt


@router.put("/detail/{prompt_id}")
async def update_prompt(
    prompt_id: str,
    body: PromptUpdate,
    user: User = Depends(get_current_active_user),
):
    db = get_db()
    existing = await prompts_dao.get_prompt(db, prompt_id)
    if not existing:
        raise HTTPException(404, "Prompt 不存在")

    if not user.is_admin and existing.get("created_by") != user.username:
        raise HTTPException(403, "只能编辑自己创建的 Prompt")

    fields = body.model_dump(exclude_none=True)
    if not user.is_admin and existing.get("status") == "approved":
        fields["status"] = "pending_review"

    prompt = await prompts_dao.update_prompt(db, prompt_id, **fields)

    if body.tags:
        await prompts_dao.bulk_upsert_tags(db, body.tags)

    await _refresh_runtime(db)
    return prompt


@router.delete("/detail/{prompt_id}")
async def delete_prompt(
    prompt_id: str,
    _: User = Depends(require_admin),
):
    db = get_db()
    ok = await prompts_dao.delete_prompt(db, prompt_id)
    if not ok:
        raise HTTPException(404, "Prompt 不存在")
    await _refresh_runtime(db)
    return {"ok": True}


# ═══════════════════════════════════════════
#  审核接口（仅 admin）
# ═══════════════════════════════════════════

@router.post("/detail/{prompt_id}/submit-review")
async def submit_for_review(
    prompt_id: str,
    _: User = Depends(get_current_active_user),
):
    db = get_db()
    prompt = await prompts_dao.submit_for_review(db, prompt_id)
    if not prompt:
        raise HTTPException(400, "只有 draft 或 rejected 状态的 Prompt 可以提交审核")
    await _refresh_runtime(db)
    return prompt


@router.get("/review/pending")
async def list_pending_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: User = Depends(require_admin),
):
    db = get_db()
    return await prompts_dao.list_prompts(
        db,
        status="pending_review",
        page=page,
        page_size=page_size,
    )


@router.post("/detail/{prompt_id}/review")
async def review_prompt(
    prompt_id: str,
    body: ReviewRequest,
    user: User = Depends(require_admin),
):
    db = get_db()
    prompt = await prompts_dao.review_prompt(
        db,
        prompt_id,
        approved=body.approved,
        reviewer=user.username,
        comment=body.comment,
    )
    if not prompt:
        raise HTTPException(400, "只能审核 pending_review 状态的 Prompt")
    await _refresh_runtime(db)
    return prompt


@router.post("/detail/{prompt_id}/archive")
async def archive_prompt(
    prompt_id: str,
    _: User = Depends(require_admin),
):
    db = get_db()
    prompt = await prompts_dao.archive_prompt(db, prompt_id)
    if not prompt:
        raise HTTPException(404, "Prompt 不存在")
    await _refresh_runtime(db)
    return prompt


# ═══════════════════════════════════════════
#  批量操作 & 导出（admin）
# ═══════════════════════════════════════════

class BatchDeleteRequest(BaseModel):
    prompt_ids: list[str] = Field(..., min_length=1, max_length=100)


class BatchReviewRequest(BaseModel):
    prompt_ids: list[str] = Field(..., min_length=1, max_length=100)
    approved: bool
    comment: str = ""


class BatchTagRequest(BaseModel):
    prompt_ids: list[str] = Field(..., min_length=1, max_length=100)
    add_tags: list[str] = []
    remove_tags: list[str] = []


@router.post("/batch/delete")
async def batch_delete(
    body: BatchDeleteRequest,
    _: User = Depends(require_admin),
):
    db = get_db()
    deleted = 0
    for pid in body.prompt_ids:
        if await prompts_dao.delete_prompt(db, pid):
            deleted += 1
    await _refresh_runtime(db)
    return {"deleted": deleted, "total": len(body.prompt_ids)}


@router.post("/batch/review")
async def batch_review(
    body: BatchReviewRequest,
    user: User = Depends(require_admin),
):
    db = get_db()
    reviewed = 0
    for pid in body.prompt_ids:
        r = await prompts_dao.review_prompt(
            db, pid, approved=body.approved, reviewer=user.username, comment=body.comment
        )
        if r:
            reviewed += 1
    await _refresh_runtime(db)
    return {"reviewed": reviewed, "total": len(body.prompt_ids)}


@router.post("/batch/tags")
async def batch_update_tags(
    body: BatchTagRequest,
    _: User = Depends(require_admin),
):
    db = get_db()
    from api.db.collections import PROMPTS_COLLECTION
    updated = 0
    for pid in body.prompt_ids:
        ops = {}
        if body.add_tags:
            ops["$addToSet"] = {"tags": {"$each": body.add_tags}}
        if body.remove_tags:
            ops["$pull"] = {"tags": {"$in": body.remove_tags}} if len(body.remove_tags) > 1 else {"tags": body.remove_tags[0]}
        if ops:
            r = await db[PROMPTS_COLLECTION].update_one({"prompt_id": pid}, ops)
            if r.modified_count:
                updated += 1
    if body.add_tags:
        await prompts_dao.bulk_upsert_tags(db, body.add_tags)
    await _refresh_runtime(db)
    return {"updated": updated, "total": len(body.prompt_ids)}


@router.get("/export/all")
async def export_all(
    _: User = Depends(require_admin),
):
    """导出全部 Prompts 数据（含分类、标签）"""
    db = get_db()
    categories = await prompts_dao.list_categories(db)
    tags = await prompts_dao.list_tags(db)
    result = await prompts_dao.list_prompts(db, page_size=10000)
    return {
        "categories": categories,
        "tags": tags,
        "prompts": result["items"],
        "total": result["total"],
    }
