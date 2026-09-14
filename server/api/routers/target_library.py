"""Global, historical institution library with pageable evidence and run history."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from api.auth import require_permission
from api.services.authorization import Permissions
from api.db.mongodb import get_db
from api.services import target_library as service
from api.schemas.target_library import LibraryPage, LibraryTarget, LibraryHistoryPage

router = APIRouter(dependencies=[Depends(require_permission(Permissions.PROJECTS_READ))])


@router.get("", response_model=LibraryPage)
async def list_targets(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), q: str = "", project_id: str = "", parent_id: str = "", refresh: bool = False):
    return await service.list_library(get_db(), page=page, page_size=page_size, q=q, project_id=project_id, parent_id=parent_id, refresh=refresh)


@router.get("/{target_id}", response_model=LibraryTarget)
async def target_detail(target_id: str):
    try:
        return await service.detail(get_db(), target_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{target_id}/compare")
async def compare(target_id: str, version_id: str):
    from api.services.target_library.changes import compare_previous
    try:
        return await compare_previous(get_db(), target_id, version_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{target_id}/history", response_model=LibraryHistoryPage)
async def target_history(target_id: str, kind: Literal["documents", "versions", "scans", "mobile"] = "documents", skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), document_id: str = ""):
    try:
        return await service.history(get_db(), target_id, kind=kind, skip=skip, limit=limit, document_id=document_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
