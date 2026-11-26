"""Nightly job: training roll-forward (courses complete → skills on profiles), full run logging."""
import shutil

import pytest

from app import config, jobs
from app.db import connect, one, rows
from app.migrations import ensure_schema
from app.services import demands, training
from app.services.auth import get_user


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db = tmp_path_factory.mktemp("jobs") / "coforce.db"
    shutil.copy(config.DB_PATH, db)
    c = connect(str(db))
    ensure_schema(c, refresh_residency=True)
    yield c
    c.close()


def test_training_rolls_forward_and_adds_skills(conn, monkeypatch):
    lead = get_user(conn, one(conn, "SELECT user_id FROM users WHERE app_role='lead' ORDER BY user_id LIMIT 1")["user_id"])
    hr = get_user(conn, one(conn, "SELECT user_id FROM users WHERE app_role='hr' ORDER BY user_id LIMIT 1")["user_id"])
    did = demands.create(conn, lead, {
        "role_title": "Data Engineer", "ibu_id": "BFS", "positions": 4, "grade_min": "A2", "grade_max": "B1", "exp_min": 2,
        "exp_max": 8, "locations": ["Bengaluru", "Pune", "Hyderabad", "Noida", "Chennai"], "work_mode": "Hybrid",
        "needed_by": "2027-01-15", "client_interview": False, "overprovision_factor": 2,
        "mandatory_skills": [{"skill": s} for s in ("python", "sql", "pyspark", "databricks", "dbt")]})["demand_id"]
    plan = training.decide(conn, training.create_plan(conn, lead, demand_id=did)["plan_id"], hr, True)
    enr = rows(conn, """SELECT t.emp_id, t.skill_id, c.level FROM training_enrollments t JOIN courses c USING(course_id)
                        WHERE t.plan_id=?""", (plan["plan_id"],))
    assert enr and all(e for e in enr)

    monkeypatch.setattr(config, "AS_OF_DATE", "2026-12-08")          # first batches have started
    mid = jobs.roll_training(conn)
    assert mid["enrolments_started"] > 0                              # (classroom batches only exist for groups ≥ 6)

    monkeypatch.setattr(config, "AS_OF_DATE", "2027-02-01")          # everything has finished
    out = jobs.roll_training(conn)
    assert out["enrolments_completed"] >= len(enr) - mid.get("enrolments_completed", 0) - 1
    left = one(conn, "SELECT count(*) n FROM training_enrollments WHERE plan_id=? AND status!='completed'", (plan["plan_id"],))["n"]
    assert left == 0
    for e in enr:
        lvl = one(conn, "SELECT proficiency FROM employee_skills WHERE emp_id=? AND skill_id=?", (e["emp_id"], e["skill_id"]))
        assert lvl and lvl["proficiency"] >= jobs.LEVEL_FOR_COURSE.get(e["level"], 2)
    assert one(conn, "SELECT count(*) n FROM training_batches WHERE plan_id=? AND status!='Completed'", (plan["plan_id"],))["n"] == 0


def test_status_follows_active_courses(conn):
    jobs.roll_training(conn)
    bad = one(conn, """SELECT count(*) n FROM employees e WHERE e.status='training' AND NOT EXISTS
                       (SELECT 1 FROM training_enrollments t WHERE t.emp_id=e.emp_id AND t.status='in_progress')""")["n"]
    assert bad == 0


def test_full_nightly_run_is_logged(conn):
    res = jobs.run_nightly(conn, triggered_by="test")
    assert res["status"] == "ok"
    assert set(res["detail"]) == {"training", "residency", "matching", "upskilling"}
    last = jobs.recent_runs(conn, 1)[0]
    assert last["triggered_by"] == "test" and last["status"] == "ok"
