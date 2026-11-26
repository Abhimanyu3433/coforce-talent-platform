"""Service-layer tests (no web framework needed). Run: cd backend && pytest -q"""
import shutil

import pytest

from app import config
from app.db import connect, one
from app.migrations import ensure_schema
from app.services import auth, employees, residency
from app.services.errors import BadRequest, Conflict, Forbidden, Unauthorized
from app.services.skills import get_index


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "coforce.db"
    shutil.copy(config.DB_PATH, db)
    c = connect(str(db))
    ensure_schema(c, refresh_residency=True)
    yield c
    c.close()


# ---------------- residency
def test_residency_snapshot_consistent(conn):
    s = residency.summary(conn)
    assert s["headcount"] == 10_000
    assert 10 <= s["bench_pct"] <= 14
    assert s["rotation_due"] > 0
    assert sum(b["employees"] for b in s["residency_bands"]) == s["delivery"]
    bad = one(conn, """SELECT count(*) n FROM residency_snapshot WHERE rotation_due=1
                       AND account_residency_months <= rotation_threshold_months""")["n"]
    assert bad == 0


def test_bench_people_have_no_current_project(conn):
    n = one(conn, """SELECT count(*) n FROM residency_snapshot r JOIN employees e USING(emp_id)
                     WHERE e.status IN ('bench','training') AND r.current_project_id IS NOT NULL""")["n"]
    assert n == 0


def test_employee_residency_history(conn):
    emp = residency.list_employees(conn, rotation_due=True, page_size=1)["items"][0]
    r = residency.employee_residency(conn, emp["emp_id"])
    current = [h for h in r["account_history"] if h["current"]]
    assert current and abs(current[0]["months"] - emp["account_residency_months"]) < 0.2


def test_heatmap_counts(conn):
    h = residency.heatmap(conn, "BFS")
    assert h and all(sum(a["bands"].values()) == a["headcount"] for a in h)


# ---------------- skills
@pytest.mark.parametrize("text,expected", [("pyspark", "PySpark"), ("React.js", "React"), ("adf", "Azure Data Factory"),
                                           ("k8s", "Kubernetes"), ("Snowflak", "Snowflake"), ("Spring boot", "Spring Boot")])
def test_normalize(conn, text, expected):
    assert get_index(conn).normalize(text)["match"]["name"] == expected


def test_normalize_unknown_gives_candidates(conn):
    r = get_index(conn).normalize("spark")
    assert r["match"] is not None or r["candidates"]


# ---------------- search
def test_search_with_skills(conn):
    res = employees.search(conn, skills=["PySpark", "Databricks"], min_proficiency=3, status="bench", page_size=50)
    assert res["total"] > 0
    for item in res["items"]:
        names = {m["name"] for m in item["matched_skills"]}
        assert {"PySpark", "Databricks"} <= names


def test_search_unknown_skill(conn):
    with pytest.raises(BadRequest):
        employees.search(conn, skills=["qwertyuiop"])


# ---------------- profile & self-service
def test_profile(conn):
    p = employees.profile(conn, "CF100300")
    assert p["skills"] and "residency" in p and isinstance(p["allocations"], list)


def test_add_update_remove_skill(conn):
    emp = "CF100300"
    have = {s["name"] for s in employees.profile(conn, emp)["skills"]}
    target = next(n for n in ["LangGraph", "Flutter", "Looker", "ArgoCD"] if n not in have)
    r = employees.add_or_update_skill(conn, emp, target.lower(), 2, 0.5)
    assert r["created"]
    employees.add_or_update_skill(conn, emp, target, 3)
    row = one(conn, "SELECT proficiency, source, verified FROM employee_skills WHERE emp_id=? AND skill_id=?", (emp, r["skill"]["skill_id"]))
    assert row == {"proficiency": 3, "source": "self_declared", "verified": 0}
    employees.remove_skill(conn, emp, r["skill"]["skill_id"])


def test_cannot_overwrite_project_skill(conn):
    emp = "CF100300"
    proj = next(s for s in employees.profile(conn, emp)["skills"] if s["source"] == "project")
    with pytest.raises(Conflict):
        employees.add_or_update_skill(conn, emp, proj["name"], 5)
    with pytest.raises(Forbidden):
        employees.remove_skill(conn, emp, proj["skill_id"])


def test_certification_flow(conn):
    emp = "CF100300"
    c = employees.add_certification(conn, emp, "SnowPro Core Certification", "Snowflake", ["snowflake"], "2026-10-01", "2028-10-01", "SNO-1")
    assert c["verification_status"] == "pending"
    assert any(x["cert_id"] == c["cert_id"] for x in employees.list_certifications(conn, "pending"))
    hr = one(conn, "SELECT user_id FROM users WHERE app_role='hr' LIMIT 1")["user_id"]
    with pytest.raises(Forbidden):
        employees.review_certification(conn, c["cert_id"], emp, True)
    employees.review_certification(conn, c["cert_id"], hr, True, "Checked credential")
    row = one(conn, """SELECT es.verified FROM employee_skills es JOIN skills s USING(skill_id)
                       WHERE es.emp_id=? AND s.name='Snowflake'""", (emp,))
    assert row["verified"] == 1


# ---------------- auth
def test_login_and_token(conn):
    u = one(conn, "SELECT email FROM users WHERE app_role='manager' LIMIT 1")
    user = auth.authenticate(conn, u["email"], config.DEMO_PASSWORD)
    assert user["app_role"] == "manager" and "password_hash" not in user
    payload = auth.decode_token(auth.create_token(user))
    assert payload["sub"] == user["user_id"]
    with pytest.raises(Unauthorized):
        auth.authenticate(conn, u["email"], "wrong-password")
    # password now stored hashed → demo password still works because it was saved on first login
    assert auth.authenticate(conn, u["email"], config.DEMO_PASSWORD)


def test_change_password(conn):
    u = one(conn, "SELECT user_id, email FROM users WHERE app_role='employee' LIMIT 1")
    auth.authenticate(conn, u["email"], config.DEMO_PASSWORD)
    auth.change_password(conn, u["user_id"], config.DEMO_PASSWORD, "NewPass#2026")
    with pytest.raises(Unauthorized):
        auth.authenticate(conn, u["email"], config.DEMO_PASSWORD)
    assert auth.authenticate(conn, u["email"], "NewPass#2026")
