"""
学者学术联系发现 — 查询 API

查询已入库的学者学术联系(按文章绑定的公开通讯邮箱)与文章。
采集通过统一任务框架下发(task_type=scholar_contact)，本 router 只做查询与概览。

合规边界：返回的是按「文章」绑定的公开学术通讯/联系邮箱，
         不提供整单位联系方式名单导出，不含个人电话。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import get_current_active_user
from api.db.mongodb import init_mongo, get_db
from api.dao import scholar_contact as scholar_dao
from api.schemas.pagination import (
    PageResponse,
    ScholarContactListRequest,
    ScholarArticleListRequest,
)

router = APIRouter(dependencies=[Depends(get_current_active_user)])

init_mongo()


@router.post("/projects/{project_id}/scholar-contacts")
async def list_scholar_contacts(
    project_id: str, body: ScholarContactListRequest | None = None,
):
    """分页查询学者联系(邮箱 → 文章 → 来源)。"""
    if body is None:
        body = ScholarContactListRequest(project_id=project_id)
    db = get_db()
    items, total = await scholar_dao.query_contacts(
        db, project_id, unit=body.unit,
        only_corresponding=body.only_corresponding,
        only_verified=body.only_verified,
        limit=body.limit, skip=body.skip,
    )
    return PageResponse.build(
        items=items, total=total, page=body.page, page_size=body.page_size,
    )


@router.post("/projects/{project_id}/scholar-articles")
async def list_scholar_articles(
    project_id: str, body: ScholarArticleListRequest | None = None,
):
    """分页查询已收集的文章。"""
    if body is None:
        body = ScholarArticleListRequest(project_id=project_id)
    db = get_db()
    items, total = await scholar_dao.query_articles(
        db, project_id, unit=body.unit, limit=body.limit, skip=body.skip,
    )
    return PageResponse.build(
        items=items, total=total, page=body.page, page_size=body.page_size,
    )


@router.get("/projects/{project_id}/scholar-contacts/units")
async def list_scholar_units(project_id: str):
    """按单位聚合已收集的联系/通讯计数，供概览与筛选。"""
    db = get_db()
    units = await scholar_dao.list_units(db, project_id)
    return {"units": units, "total": len(units)}
