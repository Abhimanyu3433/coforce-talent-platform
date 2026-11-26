"""Request bodies (responses are plain dicts from the service layer)."""
from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    email: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


class SkillIn(BaseModel):
    skill: str = Field(min_length=1, description="Skill name or synonym, e.g. 'pyspark' or 'ADF'")
    proficiency: int = Field(ge=1, le=5)
    years_used: float | None = Field(default=None, ge=0, le=40)
    last_used: str | None = Field(default=None, description="YYYY-MM-DD")


class CertificationIn(BaseModel):
    name: str
    issuer: str
    skills: list[str] = Field(min_length=1)
    issued_on: str
    valid_till: str | None = None
    credential_id: str | None = None
    proof_filename: str | None = Field(default=None, max_length=255)
    proof_base64: str | None = Field(default=None, max_length=7_200_000, description="PDF/PNG/JPG, base64, max 5 MB")


class FileIn(BaseModel):
    filename: str | None = Field(default=None, max_length=255)
    data_base64: str = Field(min_length=10, max_length=7_200_000, description="base64 file content, max 5 MB")


class ResumeSkillIn(BaseModel):
    skill_id: str
    proficiency: int = Field(ge=1, le=5)
    years_used: float | None = Field(default=None, ge=0, le=40)


class ResumeApplyIn(BaseModel):
    items: list[ResumeSkillIn] = Field(min_length=1, max_length=200)


class CertReviewIn(BaseModel):
    approve: bool
    note: str | None = None


# ---------------- Stage 3: demands & matching
class SkillReq(BaseModel):
    skill: str = Field(min_length=1)
    min_proficiency: int | None = Field(default=None, ge=1, le=5)
    weight: float | None = Field(default=None, gt=0, le=3)


class DemandIn(BaseModel):
    role_title: str = Field(min_length=2)
    role_family: str | None = None
    ibu_id: str | None = None
    account_id: str | None = None
    project_id: str | None = None
    positions: int = Field(ge=1, le=200)
    grade_min: str | None = None
    grade_max: str | None = None
    exp_min: float | None = Field(default=None, ge=0, le=40)
    exp_max: float | None = Field(default=None, ge=0, le=40)
    locations: list[str] = []
    work_mode: str | None = None
    remote_allowed: bool = False
    needed_by: str
    duration_months: int | None = Field(default=None, ge=1, le=60)
    shift: str | None = None
    client_interview: bool = False
    priority: str | None = "Medium"
    overprovision_factor: float | None = Field(default=1.0, ge=1.0, le=3.0)
    mandatory_skills: list[SkillReq] = []
    nice_skills: list[SkillReq] = []
    jd_text: str | None = None
    source: str | None = None


class DemandStatusIn(BaseModel):
    status: str


class MatchActionIn(BaseModel):
    action: str = Field(description="shortlist | nominate | reject | select | client_reject | reset")
    note: str | None = None


class ReleaseDecisionIn(BaseModel):
    approve: bool
    note: str | None = None
