"""End-to-end API tests (need fastapi + httpx). Run: cd backend && pytest -q"""
import shutil

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = tmp_path_factory.mktemp("api") / "coforce.db"
    shutil.copy(config.DB_PATH, db)
    config.DB_PATH = str(db)
    import os
    os.environ["COFORCE_NIGHTLY_AT"] = "off"           # no background scheduler during tests
    from app.main import app
    with TestClient(app) as c:
        yield c


def login(client, role):
    demo = client.get("/api/auth/demo-users").json()
    email = next(u["email"] for u in demo["users"] if u["app_role"] == role)
    r = client.post("/api/auth/login", json={"email": email, "password": demo["password"]})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["user"]


def test_health(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["employees"] == 10000


def test_admin_jobs(client):
    h, _ = login(client, "manager")
    assert client.get("/api/admin/jobs", headers=h).status_code == 200
    e, _ = login(client, "employee")
    assert client.post("/api/admin/jobs/nightly", headers=e).status_code == 403


def test_auth_required(client):
    assert client.get("/api/employees").status_code == 401


def test_bad_login(client):
    assert client.post("/api/auth/login", json={"email": "nobody@coforce.com", "password": "x"}).status_code == 401


def test_manager_dashboard_and_search(client):
    h, _ = login(client, "manager")
    s = client.get("/api/residency/summary", headers=h).json()
    assert s["headcount"] == 10000
    assert client.get("/api/residency/heatmap?ibu_id=BFS", headers=h).status_code == 200
    r = client.get("/api/employees", params={"skills": "pyspark,databricks", "min_proficiency": 3, "status": "bench"}, headers=h)
    assert r.status_code == 200 and r.json()["resolved_skills"] == ["PySpark", "Databricks"]
    emp = r.json()["items"][0]["emp_id"]
    assert client.get(f"/api/employees/{emp}", headers=h).json()["emp_id"] == emp


DEMAND = {"role_title": "Data Engineer", "ibu_id": "BFS", "positions": 2, "grade_min": "A2", "grade_max": "B1",
          "exp_min": 2, "exp_max": 8, "locations": ["Bengaluru", "Pune", "Hyderabad"], "work_mode": "Hybrid",
          "needed_by": "2027-01-15", "mandatory_skills": [{"skill": "pyspark", "min_proficiency": 3}, {"skill": "sql"}],
          "nice_skills": [{"skill": "databricks"}], "overprovision_factor": 1.5}


def test_demand_matching_flow(client):
    h, _ = login(client, "lead")
    s = client.post("/api/demands/supply-check", json=DEMAND, headers=h)
    assert s.status_code == 200, s.text
    assert s.json()["needed"] == 3 and s.json()["role_family"] == "DATA_ENG"
    bad = client.post("/api/demands/supply-check", json={**DEMAND, "mandatory_skills": [{"skill": "zzqqxx"}]}, headers=h)
    assert bad.status_code == 400
    r = client.post("/api/demands", json=DEMAND, headers=h)
    assert r.status_code == 200, r.text
    did = r.json()["demand_id"]
    assert client.get("/api/demands", params={"mine": True}, headers=h).json()["total"] >= 1
    m = client.get(f"/api/demands/{did}/matches", headers=h).json()
    first = m["candidates"][0]
    a = client.post(f"/api/demands/{did}/matches/{first['emp_id']}/action", json={"action": "shortlist"}, headers=h)
    assert a.status_code == 200 and a.json()["status"] == "shortlisted"
    bad = client.post(f"/api/demands/{did}/matches/{first['emp_id']}/action", json={"action": "select"}, headers=h)
    assert bad.status_code == 400
    assert client.post(f"/api/demands/{did}/match", headers=h).status_code == 200
    assert client.get("/api/approvals", headers=h).status_code == 200
    e, _ = login(client, "employee")
    assert client.get("/api/demands", headers=e).status_code == 403


def test_chat_flow(client):
    h, _ = login(client, "lead")
    assert client.get("/api/chat/demand/status", headers=h).json()["mode"] in ("rules", "gemini")
    s = client.post("/api/chat/demand/sessions", headers=h).json()
    sid = s["session_id"]
    for msg in ["I need 2 data engineers", "pyspark, sql", "3", "none", "3-6 years", "none", "Pune", "in 30 days"]:
        r = client.post(f"/api/chat/demand/sessions/{sid}/messages", json={"text": msg}, headers=h)
        assert r.status_code == 200, r.text
    r = client.post(f"/api/chat/demand/sessions/{sid}/messages", json={"text": "create demand"}, headers=h).json()
    assert r["phase"] == "created" and r["demand_id"]
    assert any(x["session_id"] == sid for x in client.get("/api/chat/demand/sessions", headers=h).json())
    e, _ = login(client, "employee")
    assert client.post("/api/chat/demand/sessions", headers=e).status_code == 403


def test_training_flow(client):
    h, _ = login(client, "lead")
    d = client.post("/api/demands", json={**DEMAND, "positions": 3, "needed_by": "2027-01-29"}, headers=h).json()
    pv = client.post("/api/training/plans/preview", json={"demand_id": d["demand_id"]}, headers=h)
    assert pv.status_code == 200, pv.text
    r = client.post("/api/training/plans", json={"demand_id": d["demand_id"]}, headers=h)
    if r.status_code == 400:        # nothing to train for this demand: everyone already fits
        return
    pid = r.json()["plan_id"]
    assert client.post(f"/api/training/plans/{pid}/decision", json={"approve": True}, headers=h).status_code == 403
    hh, _ = login(client, "hr")
    assert client.post(f"/api/training/plans/{pid}/decision", json={"approve": True}, headers=hh).json()["status"] == "approved"
    assert client.get("/api/training/calendar", headers=h).status_code == 200
    assert client.get("/api/training/forecast", headers=h).status_code == 200
    assert client.get("/api/training/onboarding", headers=h).status_code == 200


def test_upskilling_flow(client):
    e, me = login(client, "employee")
    recs = client.get("/api/me/upskilling", headers=e).json()
    assert recs["emp_id"] == me["user_id"]
    if recs["recommendations"]:
        sid = recs["recommendations"][0]["skill_id"]
        r = client.post(f"/api/me/upskilling/{sid}/start", json={"mode": "self-paced"}, headers=e)
        assert r.status_code in (200, 400), r.text
    assert client.get("/api/upskilling/bench", headers=e).status_code == 403
    h, _ = login(client, "manager")
    ov = client.get("/api/upskilling/bench", params={"page_size": 5}, headers=h).json()
    assert "top_skills" in ov and len(ov["people"]) <= 5
    assert client.get(f"/api/employees/{me['user_id']}/upskilling", headers=h).status_code == 200


def test_jd_pdf_upload(client):
    pytest.importorskip("pypdf")
    import base64
    from tests.test_jd_pdf import JD, make_pdf
    h, _ = login(client, "lead")
    sid = client.post("/api/chat/demand/sessions", headers=h).json()["session_id"]
    r = client.post(f"/api/chat/demand/sessions/{sid}/jd-pdf", headers=h,
                    json={"filename": "jd.pdf", "data_base64": base64.b64encode(make_pdf(JD)).decode()})
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["role_title"] == "Senior Data Engineer"
    bad = client.post(f"/api/chat/demand/sessions/{sid}/jd-pdf", headers=h,
                      json={"filename": "x.pdf", "data_base64": base64.b64encode(b"not a pdf at all").decode()})
    assert bad.status_code == 400


def test_employee_permissions_and_self_service(client):
    h, user = login(client, "employee")
    assert client.get("/api/employees", headers=h).status_code == 403
    assert client.get("/api/residency/summary", headers=h).status_code == 403
    assert client.get("/api/me/profile", headers=h).json()["emp_id"] == user["user_id"]
    other = "CF100001" if user["user_id"] != "CF100001" else "CF100002"
    assert client.get(f"/api/employees/{other}", headers=h).status_code == 403
    r = client.post("/api/me/skills", json={"skill": "langgraph", "proficiency": 2}, headers=h)
    assert r.status_code in (200, 409), r.text
    r = client.post("/api/me/certifications", headers=h, json={"name": "SnowPro Core Certification", "issuer": "Snowflake",
                                                                "skills": ["snowflake"], "issued_on": "2026-09-01"})
    assert r.status_code == 200 and r.json()["verification_status"] == "pending"
    cert = r.json()["cert_id"]
    hh, _ = login(client, "hr")
    assert client.post(f"/api/certifications/{cert}/review", json={"approve": True}, headers=hh).json()["verification_status"] == "verified"
    assert client.post(f"/api/certifications/{cert}/review", json={"approve": True}, headers=h).status_code == 403
