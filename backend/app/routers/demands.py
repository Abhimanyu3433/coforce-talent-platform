from fastapi import APIRouter, Depends, Query

from ..deps import current_user, get_db, privileged
from ..schemas import DemandIn, DemandStatusIn, MatchActionIn, ReleaseDecisionIn
from ..services import demands as svc
from ..services import matching

router = APIRouter(prefix="/api", tags=["demands & matching"])


@router.get("/demands")
def list_demands(status: str | None = "Open", ibu_id: str | None = None, mine: bool = False, q: str | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                 user=Depends(privileged), conn=Depends(get_db)):
    return svc.list_demands(conn, status or None, ibu_id, user["user_id"] if mine else None, q, page, page_size)


@router.post("/demands")
def create_demand(body: DemandIn, user=Depends(privileged), conn=Depends(get_db)):
    return svc.create(conn, user, body.model_dump())


@router.post("/demands/supply-check")
def supply_check(body: DemandIn, user=Depends(privileged), conn=Depends(get_db)):
    """Live check while a JD is being written: how many internal people fit, and which skill is the bottleneck."""
    p = svc.normalize_payload(conn, body.model_dump())
    return matching.supply_check(conn, svc.to_spec(conn, p)) | {"role_family": p["role_family"]}


@router.get("/demands/{demand_id}")
def get_demand(demand_id: str, user=Depends(privileged), conn=Depends(get_db)):
    return svc.get(conn, demand_id)


@router.patch("/demands/{demand_id}/status")
def set_status(demand_id: str, body: DemandStatusIn, user=Depends(privileged), conn=Depends(get_db)):
    return svc.set_status(conn, demand_id, body.status)


@router.post("/demands/{demand_id}/match")
def run_match(demand_id: str, include_all_allocated: bool = False, user=Depends(privileged), conn=Depends(get_db)):
    return matching.run_and_store(conn, demand_id, user["user_id"], include_all_allocated)


@router.get("/demands/{demand_id}/matches")
def get_matches(demand_id: str, user=Depends(privileged), conn=Depends(get_db)):
    return matching.list_matches(conn, demand_id)


@router.post("/demands/{demand_id}/matches/{emp_id}/action")
def match_action(demand_id: str, emp_id: str, body: MatchActionIn, user=Depends(privileged), conn=Depends(get_db)):
    return matching.act(conn, demand_id, emp_id, body.action, user, body.note)


@router.get("/approvals")
def approvals(user=Depends(current_user), conn=Depends(get_db)):
    return matching.pending_approvals(conn, user)


@router.post("/approvals/{demand_id}/{emp_id}")
def decide(demand_id: str, emp_id: str, body: ReleaseDecisionIn, user=Depends(current_user), conn=Depends(get_db)):
    return matching.decide_release(conn, demand_id, emp_id, user, body.approve, body.note)
