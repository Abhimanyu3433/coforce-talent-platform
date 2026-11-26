from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..db import rows
from ..deps import current_user, get_db, hr_only, privileged
from ..services import training as svc
from ..services.errors import BadRequest

router = APIRouter(prefix="/api/training", tags=["training planner"])


class PlanIn(BaseModel):
    demand_id: str | None = None
    skill_id: str | None = None
    overprovision_factor: float | None = Field(default=None, ge=1.0, le=3.0)
    include_rotation: bool = False
    extra_bench: bool = False
    size: int | None = Field(default=None, ge=1, le=200)


class DecisionIn(BaseModel):
    approve: bool
    note: str | None = None
    overprovision_factor: float | None = Field(default=None, ge=1.0, le=3.0)


@router.post("/plans/preview")
def preview(body: PlanIn, user=Depends(privileged), conn=Depends(get_db)):
    if body.demand_id:
        return svc.plan_for_demand(conn, body.demand_id, body.overprovision_factor, body.include_rotation, body.extra_bench)
    if body.skill_id:
        return svc.plan_for_skill(conn, body.skill_id, body.size)
    raise BadRequest("Give a demand_id or a skill_id")


@router.post("/plans")
def create(body: PlanIn, user=Depends(privileged), conn=Depends(get_db)):
    return svc.create_plan(conn, user, body.demand_id, body.skill_id, body.overprovision_factor,
                           body.include_rotation, body.extra_bench, body.size)


@router.get("/plans")
def list_plans(status: str | None = None, mine: bool = False, user=Depends(privileged), conn=Depends(get_db)):
    return svc.list_plans(conn, status, user["user_id"] if mine else None)


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, user=Depends(privileged), conn=Depends(get_db)):
    return svc.get_plan(conn, plan_id)


@router.post("/plans/{plan_id}/decision")
def decide(plan_id: str, body: DecisionIn, user=Depends(hr_only), conn=Depends(get_db)):
    return svc.decide(conn, plan_id, user, body.approve, body.note, body.overprovision_factor)


@router.post("/plans/{plan_id}/cancel")
def cancel(plan_id: str, user=Depends(privileged), conn=Depends(get_db)):
    return svc.cancel(conn, plan_id, user)


@router.get("/calendar")
def calendar(start: str | None = None, days: int = Query(90, ge=7, le=365), user=Depends(privileged), conn=Depends(get_db)):
    return svc.calendar(conn, start, days)


@router.get("/forecast")
def forecast(months_back: int = Query(6, ge=1, le=24), top: int = Query(15, ge=1, le=50),
             user=Depends(privileged), conn=Depends(get_db)):
    return svc.forecast(conn, months_back, top)


@router.get("/forecast/{skill_id}/candidates")
def forecast_candidates(skill_id: str, limit: int = Query(25, ge=1, le=200), user=Depends(privileged), conn=Depends(get_db)):
    return svc.forecast_candidates(conn, skill_id, limit)


@router.get("/onboarding")
def onboarding(user=Depends(privileged), conn=Depends(get_db)):
    return svc.onboarding(conn)


@router.get("/my")
def my_training(user=Depends(current_user), conn=Depends(get_db)):
    """Upcoming and current scheduled training for the signed-in employee."""
    return rows(conn, """SELECT t.enrollment_id, c.title, t.mode, t.start_date, t.end_date, t.status, t.source, t.batch_id, t.plan_id
                         FROM training_enrollments t JOIN courses c USING(course_id)
                         WHERE t.emp_id=? AND t.status IN ('scheduled','in_progress') ORDER BY t.start_date""", (user["user_id"],))
