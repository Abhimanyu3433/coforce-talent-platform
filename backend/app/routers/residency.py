from fastapi import APIRouter, Depends, Query

from ..deps import get_db, privileged, require_roles
from ..services import residency as svc

router = APIRouter(prefix="/api/residency", tags=["residency"])


@router.get("/summary")
def summary(ibu_id: str | None = None, account_id: str | None = None, role_family: str | None = None,
            user=Depends(privileged), conn=Depends(get_db)):
    return svc.summary(conn, ibu_id, account_id, role_family)


@router.get("/heatmap")
def heatmap(ibu_id: str | None = None, user=Depends(privileged), conn=Depends(get_db)):
    return svc.heatmap(conn, ibu_id)


@router.get("/employees")
def residency_employees(ibu_id: str | None = None, account_id: str | None = None, band: str | None = None,
                        rotation_due: bool | None = None, status: str | None = None, q: str | None = None,
                        sort: str = "residency", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                        user=Depends(privileged), conn=Depends(get_db)):
    return svc.list_employees(conn, ibu_id, account_id, band, rotation_due, status, q, sort, page, page_size)


@router.post("/refresh")
def refresh(user=Depends(require_roles("manager", "hr")), conn=Depends(get_db)):
    return svc.refresh(conn)
