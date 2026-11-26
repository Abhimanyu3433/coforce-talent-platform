from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response

from ..deps import can_view_employee, current_user, get_db, privileged
from ..schemas import CertificationIn, FileIn, ResumeApplyIn, SkillIn
from ..services import employees as svc
from ..services import resume

router = APIRouter(prefix="/api", tags=["employees"])


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in v.split(",") if x.strip()] if v else []


@router.get("/employees")
def search_employees(
    q: str | None = None, ibu_id: str | None = None, status: str | None = None, role_family: str | None = None,
    grade: str | None = None, location: str | None = None, account_id: str | None = None,
    skills: str | None = Query(None, description="Comma-separated skill names/synonyms"),
    min_proficiency: int = Query(1, ge=1, le=5), skill_mode: str = Query("all", pattern="^(all|any)$"),
    verified_only: bool = False, rotation_due: bool | None = None,
    min_exp: float | None = None, max_exp: float | None = None,
    sort: str = "name", page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
    user=Depends(privileged), conn=Depends(get_db),
):
    return svc.search(conn, q=q, ibu_id=ibu_id, status=status, role_family=role_family, grade=grade, location=location,
                      account_id=account_id, skills=_csv(skills), min_proficiency=min_proficiency, skill_mode=skill_mode,
                      verified_only=verified_only, rotation_due=rotation_due, min_exp=min_exp, max_exp=max_exp,
                      sort=sort, page=page, page_size=page_size)


@router.get("/employees/{emp_id}")
def get_employee(emp_id: str, user=Depends(current_user), conn=Depends(get_db)):
    can_view_employee(user, emp_id)
    return svc.profile(conn, emp_id)


# ---------------- self-service ("me")
@router.get("/me/profile")
def my_profile(user=Depends(current_user), conn=Depends(get_db)):
    return svc.profile(conn, user["user_id"])


@router.post("/me/skills")
def add_skill(body: SkillIn, user=Depends(current_user), conn=Depends(get_db)):
    return svc.add_or_update_skill(conn, user["user_id"], body.skill, body.proficiency, body.years_used, body.last_used)


@router.delete("/me/skills/{skill_id}")
def delete_skill(skill_id: str, user=Depends(current_user), conn=Depends(get_db)):
    svc.remove_skill(conn, user["user_id"], skill_id)
    return {"ok": True}


@router.post("/me/certifications")
def add_cert(body: CertificationIn, user=Depends(current_user), conn=Depends(get_db)):
    return svc.add_certification(conn, user["user_id"], body.name, body.issuer, body.skills, body.issued_on,
                                 body.valid_till, body.credential_id, body.proof_filename, body.proof_base64)


@router.post("/me/certifications/{cert_id}/proof")
def attach_proof(cert_id: str, body: FileIn, user=Depends(current_user), conn=Depends(get_db)):
    return svc.attach_proof(conn, user["user_id"], cert_id, body.filename, body.data_base64)


@router.get("/certifications/{cert_id}/proof")
def open_proof(cert_id: str, user=Depends(current_user), conn=Depends(get_db)):
    data, media_type, name = svc.get_proof(conn, user, cert_id)
    return Response(content=data, media_type=media_type, headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}", "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store"})


# ---------------- resume → suggested skills (nothing is saved until /apply)
@router.post("/me/resume/parse")
def parse_resume(body: FileIn, user=Depends(current_user), conn=Depends(get_db)):
    return resume.parse(conn, user["user_id"], body.filename, body.data_base64)


@router.post("/me/resume/apply")
def apply_resume(body: ResumeApplyIn, user=Depends(current_user), conn=Depends(get_db)):
    return resume.apply(conn, user["user_id"], [i.model_dump() for i in body.items])
