from fastapi import APIRouter, Depends, HTTPException, Query
from api.auth import require_permission
from api.services.authorization import Permissions
from api.db.mongodb import get_db
from api.dao import persona_coverage as dao
from api.schemas.persona_coverage import CoverageStart
from api.services.persona_coverage.service import coverage, start_coverage

router = APIRouter(dependencies=[Depends(require_permission(Permissions.AI_USE))])


@router.get("")
async def get_coverage():
    return await coverage(get_db())


@router.post("/start")
async def start(req: CoverageStart):
    try:
        return await start_coverage(get_db(), industry_codes=req.industry_codes, minimum_personas=req.minimum_personas, generation_mode=req.generation_mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/organizations")
async def organizations(industry_code: str = "", skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    return await dao.list_facts(get_db(), industry_code, skip, limit)
