from fastapi import APIRouter, Depends, Query

from .. import jobs
from ..deps import get_db, require_roles

router = APIRouter(prefix="/api/admin", tags=["admin"])
admins = require_roles("manager", "hr")


@router.get("/jobs")
def job_runs(limit: int = Query(20, ge=1, le=200), user=Depends(admins), conn=Depends(get_db)):
    return jobs.recent_runs(conn, limit)


@router.post("/jobs/nightly")
def run_now(user=Depends(admins), conn=Depends(get_db)):
    """Run the nightly maintenance now (takes ~15-20 s)."""
    return jobs.run_nightly(conn, triggered_by=user["user_id"])
