"""Skills lookup, metadata for filters, and HR certification review."""
from fastapi import APIRouter, Depends, Query

from .. import config
from ..db import rows
from ..deps import current_user, get_db, hr_only
from ..schemas import CertReviewIn
from ..services import employees as emp_svc
from ..services.skills import get_index

router = APIRouter(prefix="/api", tags=["reference"])


@router.get("/skills/search")
def skill_search(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=50), types: str | None = None,
                 user=Depends(current_user), conn=Depends(get_db)):
    return get_index(conn).search(q, limit, types.split(",") if types else None)


@router.get("/skills/normalize")
def skill_normalize(text: str, user=Depends(current_user), conn=Depends(get_db)):
    return get_index(conn).normalize(text)


@router.get("/meta")
def meta(user=Depends(current_user), conn=Depends(get_db)):
    return emp_svc.meta(conn)


@router.get("/projects")
def projects(ibu_id: str | None = None, account_id: str | None = None, active: bool = True,
             user=Depends(current_user), conn=Depends(get_db)):
    where, p = ["1=1"], []
    if ibu_id:
        where.append("ibu_id=?"); p.append(ibu_id)
    if account_id:
        where.append("account_id=?"); p.append(account_id)
    if active:
        where.append("end_date > ?"); p.append(config.as_of().isoformat())
    return rows(conn, f"""SELECT project_id, name, project_type, account_id, ibu_id, work_mode, shift, delivery_locations,
                                 onsite_location, start_date, end_date FROM projects WHERE {' AND '.join(where)} ORDER BY name""", p)


@router.get("/certifications")
def certifications(status: str | None = "pending", ibu_id: str | None = None, limit: int = Query(200, le=1000),
                   user=Depends(hr_only), conn=Depends(get_db)):
    return emp_svc.list_certifications(conn, status or None, ibu_id, limit)


@router.post("/certifications/{cert_id}/review")
def review(cert_id: str, body: CertReviewIn, user=Depends(hr_only), conn=Depends(get_db)):
    return emp_svc.review_certification(conn, cert_id, user["user_id"], body.approve, body.note)
