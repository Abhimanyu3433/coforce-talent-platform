"""Training pipeline planner: scheduling invariants, plan lifecycle with HR approval, forecast, onboarding."""
import shutil
from datetime import date

import pytest

from app import config
from app.db import connect, one, rows
from app.migrations import ensure_schema
from app.services import demands, training
from app.services.auth import get_user
from app.services.errors import BadRequest, Forbidden


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db = tmp_path_factory.mktemp("tr") / "coforce.db"
    shutil.copy(config.DB_PATH, db)
    c = connect(str(db))
    ensure_schema(c, refresh_residency=True)
    yield c
    c.close()


def user(conn, role, n=0):
    return get_user(conn, rows(conn, "SELECT user_id FROM users WHERE app_role=? ORDER BY user_id LIMIT 1 OFFSET ?", (role, n))[0]["user_id"])


def make_demand(conn, lead, skills=("python", "sql", "pyspark", "databricks", "dbt"), needed_by="2026-12-28", positions=10, factor=2.0):
    return demands.create(conn, lead, {
        "role_title": "Data Engineer", "ibu_id": "BFS", "positions": positions, "grade_min": "A2", "grade_max": "B1",
        "exp_min": 2, "exp_max": 8, "locations": ["Bengaluru", "Pune", "Hyderabad", "Noida", "Chennai"], "work_mode": "Hybrid",
        "needed_by": needed_by, "client_interview": False, "mandatory_skills": [{"skill": s} for s in skills],
        "overprovision_factor": factor})["demand_id"]


# ---------------- calendar helpers
def test_workdays():
    fri = date(2026, 11, 27)
    assert training.next_workday(date(2026, 11, 28)) == date(2026, 11, 30)           # Sat → Mon
    assert training.end_of(date(2026, 11, 30), 5) == date(2026, 12, 4)               # Mon + 5 days → Fri
    assert training.end_of(fri, 2) == date(2026, 11, 30)                             # Fri, Mon
    assert training.add_workdays(date(2026, 11, 25), 3) == date(2026, 11, 30)


# ---------------- plan quality
def test_plan_invariants(conn):
    lead = user(conn, "lead")
    did = make_demand(conn, lead, skills=("python", "sql", "pyspark", "databricks", "dbt", "kafka"), needed_by="2027-01-20")
    p = training.plan_for_demand(conn, did)
    assert p["target"] == 20 and p["ready_count"] + p["trainees_planned"] >= p["covered"]
    cat = training.catalog(conn)
    by_key = {b["key"]: b for b in p["batches"]}
    for b in p["batches"]:
        s, e = date.fromisoformat(b["start_date"]), date.fromisoformat(b["end_date"])
        assert s.weekday() < 5 and e.weekday() < 5 and s >= date.fromisoformat(p["plan_start"])
        assert b["size"] <= training.MAX_BATCH
        if b["mode"] == "instructor-led":
            assert b["size"] >= training.MIN_CLASSROOM or len(b["learners"]) == b["size"]
    for t in p["trainees"]:
        track = [by_key[x["batch"]] for x in t["track"]]
        assert {b["skill_id"] for b in track} == {g["skill_id"] for g in t["gaps"]}
        for a, b in zip(track, track[1:]):                       # no overlap within one person's track
            assert date.fromisoformat(b["start_date"]) > date.fromisoformat(a["end_date"])
        names = [b["skill_id"] for b in track]
        for i, sid in enumerate(names):                          # prerequisites before dependants
            assert not (cat.prereq[sid] & set(names[i + 1:]))
        if p["feasible"]:
            assert t["ready_by"] <= p["deadline"]


def test_user_example_20_people_for_10_positions(conn):
    lead = user(conn, "lead")
    did = make_demand(conn, lead)
    p = training.plan_for_demand(conn, did)
    assert p["feasible"] and p["covered"] == 20 and p["shortfall"] == 0
    assert set(p["gap_summary"]) <= {"dbt", "Databricks", "PySpark", "SQL", "Python"}
    more = training.plan_for_demand(conn, did, extra_bench=True)
    assert more["trainees_planned"] > p["trainees_planned"]
    assert all(t["on_time"] for t in more["trainees"])


def test_tight_deadline_reports_shortfall(conn):
    lead = user(conn, "lead")
    did = make_demand(conn, lead, skills=("python", "pyspark", "databricks", "kafka", "dbt", "snowflake"), needed_by="2026-12-04")
    p = training.plan_for_demand(conn, did)
    assert not p["feasible"] and p["shortfall"] > 0 and p["advice"]


# ---------------- lifecycle
def test_plan_lifecycle_with_hr(conn):
    lead, hr = user(conn, "lead"), user(conn, "hr")
    did = make_demand(conn, lead, positions=4)
    plan = training.create_plan(conn, lead, demand_id=did)
    assert plan["status"] == "pending"
    with pytest.raises(BadRequest):
        training.create_plan(conn, lead, demand_id=did)                    # one active plan per demand
    with pytest.raises(Forbidden):
        training.cancel(conn, plan["plan_id"], user(conn, "lead", 1))       # not the creator
    out = training.decide(conn, plan["plan_id"], hr, True, "ok", overprovision_factor=1.5)   # HR changes the factor
    assert out["status"] == "approved" and out["overprovision_factor"] == 1.5 and out["target"] == 6
    d = one(conn, "SELECT overprovision_factor, overprovision_approved_by FROM demands WHERE demand_id=?", (did,))
    assert d == {"overprovision_factor": 1.5, "overprovision_approved_by": hr["user_id"]}
    n_enr = one(conn, "SELECT count(*) n FROM training_enrollments WHERE plan_id=? AND status='scheduled'", (out["plan_id"],))["n"]
    assert n_enr == sum(len(b["learners"]) for b in out["batches"])
    ilt = sum(1 for b in out["batches"] if b["mode"] == "instructor-led")
    assert one(conn, "SELECT count(*) n FROM training_batches WHERE plan_id=?", (out["plan_id"],))["n"] == ilt
    sl = one(conn, "SELECT count(*) n FROM matches WHERE demand_id=? AND status='shortlisted'", (did,))["n"]
    assert sl >= 1
    # trainees in an active plan are not offered to another plan
    busy = {t["emp_id"] for t in out["trainees"]}
    did2 = make_demand(conn, lead, positions=4)
    p2 = training.plan_for_demand(conn, did2)
    assert not busy & {t["emp_id"] for t in p2["trainees"]}
    with pytest.raises(BadRequest):
        training.decide(conn, out["plan_id"], hr, True)                     # already decided
    training.cancel(conn, out["plan_id"], lead)
    assert one(conn, "SELECT count(*) n FROM training_enrollments WHERE plan_id=? AND status='scheduled'", (out["plan_id"],))["n"] == 0


def test_reject_and_self_approval(conn):
    lead, hr = user(conn, "lead"), user(conn, "hr")
    gaps = ("python", "pyspark", "databricks", "dbt", "kafka")      # skills that always leave someone to train
    did = make_demand(conn, lead, skills=gaps, needed_by="2027-02-15", positions=3)
    plan = training.create_plan(conn, lead, demand_id=did)
    assert training.decide(conn, plan["plan_id"], hr, False, "budget")["status"] == "rejected"
    did2 = make_demand(conn, hr, skills=gaps, needed_by="2027-02-15", positions=3)
    plan2 = training.create_plan(conn, hr, demand_id=did2)
    with pytest.raises(Forbidden):
        training.decide(conn, plan2["plan_id"], hr, True)


# ---------------- forecast, onboarding, calendar
def test_forecast_and_skill_plan(conn):
    f = training.forecast(conn, top=20)
    assert isinstance(f, list)
    sid = one(conn, "SELECT skill_id FROM skills WHERE name='Databricks'")["skill_id"]
    p = training.plan_for_skill(conn, sid, size=12)
    assert p["plan_type"] == "forecast" and 0 < p["trainees_planned"] <= 12
    assert all(t["gaps"][0]["skill_id"] == sid for t in p["trainees"])
    saved = training.create_plan(conn, user(conn, "manager"), skill_id=sid, size=8)
    assert saved["status"] == "pending" and saved["demand_id"] is None


def test_onboarding_and_calendar(conn):
    o = training.onboarding(conn)
    assert o and all(g["curriculum"] for g in o)
    assert all(0 <= h["progress"] <= 100 for g in o for h in g["hires"])
    cal = training.calendar(conn, days=120)
    assert any(b["status"] == "Proposed" for b in cal["batches"])          # pending plans show up as proposals
