from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from api.auth import get_current_active_user
from api.dao import project_groups as project_groups_dao
from api.db.mongodb import get_db
from api.models.projects import (
    ProjectGroupCreate,
    ProjectGroupOut,
    ProjectGroupUpdate,
)


router = APIRouter(dependencies=[Depends(get_current_active_user)])


def _group_out(doc: dict) -> ProjectGroupOut:
    return ProjectGroupOut.model_validate(doc)


@router.get("", response_model=list[ProjectGroupOut])
async def list_project_groups() -> list[ProjectGroupOut]:
    docs = await project_groups_dao.list_groups(get_db())
    return [_group_out(doc) for doc in docs]


@router.post("", response_model=ProjectGroupOut, status_code=201)
async def create_project_group(body: ProjectGroupCreate) -> ProjectGroupOut:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="分组名称不能为空")
    try:
        doc = await project_groups_dao.create_group(
            get_db(),
            name=name,
            description=body.description.strip(),
            sort_order=body.sort_order,
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="分组名称已存在") from exc
    return _group_out(doc)


@router.patch("/{group_id}", response_model=ProjectGroupOut)
async def update_project_group(
    group_id: str,
    body: ProjectGroupUpdate,
) -> ProjectGroupOut:
    patch = body.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in patch:
        patch["name"] = str(patch["name"]).strip()
        if not patch["name"]:
            raise HTTPException(status_code=422, detail="分组名称不能为空")
    if "description" in patch:
        patch["description"] = str(patch["description"]).strip()
    try:
        doc = await project_groups_dao.update_group(get_db(), group_id, patch)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="分组名称已存在") from exc
    if not doc:
        raise HTTPException(status_code=404, detail="项目分组不存在")
    return _group_out(doc)


@router.delete("/{group_id}")
async def delete_project_group(group_id: str) -> dict[str, int | bool]:
    deleted, ungrouped_count = await project_groups_dao.delete_group(
        get_db(), group_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="项目分组不存在")
    return {"ok": True, "ungrouped_count": ungrouped_count}
