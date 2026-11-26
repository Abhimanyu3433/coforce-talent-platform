from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..deps import can_view_employee, current_user, get_db, privileged, require_roles
from ..services import upskilling as svc

router = APIRouter(prefix="/api", tags=["upskilling advisor"])


class StartIn(BaseModel):
    mode: str = "self-paced"           # self-paced | instructor-led
    batch_id: str | None = None        # join an open scheduled batch for the final skill


@router.get("/me/upskilling")
def my_recommendations(top: int = Query(5, ge=1, le=10), user=Depends(current_user), conn=Depends(get_db)):
    return svc.for_employee(conn, user["user_id"], top)


@router.post("/me/upskilling/{skill_id}/start")
def start(skill_id: str, body: StartIn, user=Depends(current_user), conn=Depends(get_db)):
    return svc.start_learning(conn, user["user_id"], skill_id, body.mode, body.batch_id)


@router.get("/employees/{emp_id}/upskilling")
def employee_recommendations(emp_id: str, top: int = Query(5, ge=1, le=10), user=Depends(current_user), conn=Depends(get_db)):
    can_view_employee(user, emp_id)
    return svc.for_employee(conn, emp_id, top)


@router.get("/upskilling/bench")
def bench(ibu_id: str | None = None, role_family: str | None = None, min_aging: int | None = None,
          page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), user=Depends(privileged), conn=Depends(get_db)):
    return svc.bench_overview(conn, ibu_id, role_family, min_aging, page, page_size)


@router.post("/upskilling/refresh")
def refresh(user=Depends(require_roles("manager", "hr")), conn=Depends(get_db)):
    return svc.refresh_bench(conn)
