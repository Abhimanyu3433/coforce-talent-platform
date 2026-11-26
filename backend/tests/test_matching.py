"""Matching engine, demand creation and release-approval workflow."""
import shutil

import pytest

from app import config
from app.db import connect, one, rows
from app.migrations import ensure_schema
from app.services import demands, matching
from app.services.auth import get_user
from app.services.errors import BadRequest, Forbidden


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db = tmp_path_factory.mktemp("m") / "coforce.db"
    shutil.copy(config.DB_PATH, db)
    c = connect(str(db))
    ensure_schema(c, refresh_residency=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def lead(conn):
    return get_user(conn, one(conn, "SELECT user_id FROM users WHERE app_role='lead' LIMIT 1")["user_id"])


def de_payload(**kw):
    p = {"role_title": "Data Engineer", "ibu_id": "BFS", "positions": 3, "grade_min": "A2", "grade_max": "B1",
         "exp_min": 2, "exp_max": 8, "locations": ["Bengaluru", "Pune", "Hyderabad", "Noida"], "work_mode": "Hybrid",
         "needed_by": "2027-01-10", "mandatory_skills": [{"skill": "pyspark", "min_proficiency": 3}, {"skill": "SQL", "min_proficiency": 3}],
         "nice_skills": ["databricks", "airflow"], "overprovision_factor": 2}
    p.update(kw)
    return p


def test_ranking_and_filters(conn):
    spec = demands.to_spec(conn, demands.normalize_payload(conn, de_payload()))
    r = matching.run(conn, spec, limit=200)
    c = r["candidates"]
    assert c and r["eligible"] == len(c) or len(c) == 200
    assert [x["score"] for x in c] == sorted((x["score"] for x in c), reverse=True)
    for x in c:
        assert x["mandatory_met"] / x["mandatory_total"] >= 0 and x["status"] != "notice"
        lo, hi = matching.GRADES.index("A2") - 1, matching.GRADES.index("B1")
        assert lo <= matching.GRADES.index(x["grade"]) <= hi
        assert sum(b["points"] for b in x["breakdown"].values()) == pytest.approx(x["score"], abs=0.3) or x["score"] in (0, 100)
    # among people with equal non-skill profile, meeting all mandatory skills should outrank missing ones on average
    full = [x["score"] for x in c if x["mandatory_met"] == x["mandatory_total"]]
    part = [x["score"] for x in c if x["mandatory_met"] < x["mandatory_total"]]
    if full and part:
        assert sum(full) / len(full) > sum(part) / len(part)


def test_gap_analysis_has_training_plan(conn):
    spec = demands.to_spec(conn, demands.normalize_payload(conn, de_payload(
        mandatory_skills=[{"skill": "pyspark", "min_proficiency": 3}, {"skill": "databricks", "min_proficiency": 3},
                          {"skill": "dbt", "min_proficiency": 2}])))
    c = matching.run(conn, spec, limit=300)["candidates"]
    with_gaps = [x for x in c if x["gaps"]]
    assert with_gaps
    g = with_gaps[0]
    assert g["training_days"] == sum(x["training_days"] for x in g["gaps"]) > 0
    assert g["ready_by"] >= g["availability_date"]


def test_location_filter(conn):
    spec = demands.to_spec(conn, demands.normalize_payload(conn, de_payload(locations=["Kolkata"], remote_allowed=False)))
    for x in matching.run(conn, spec, limit=500)["candidates"]:
        assert x["base_location"] == "Kolkata" or "relocation" in x["flags"]


def test_supply_check(conn):
    p = demands.normalize_payload(conn, de_payload(mandatory_skills=["pyspark", "sql", "databricks", "kafka"]))
    s = matching.supply_check(conn, demands.to_spec(conn, p))
    assert s["needed"] == 6 and s["verdict"] in ("healthy", "trainable", "scarce")
    assert len(s["relaxations"]) == 4 and all(x["gain"] >= 0 for x in s["relaxations"])


def test_payload_validation(conn):
    with pytest.raises(BadRequest):
        demands.normalize_payload(conn, de_payload(needed_by="2020-01-01"))
    with pytest.raises(BadRequest):
        demands.normalize_payload(conn, de_payload(mandatory_skills=[]))
    with pytest.raises(BadRequest):
        demands.normalize_payload(conn, de_payload(mandatory_skills=["notaskill zzz"]))
    p = demands.normalize_payload(conn, de_payload(role_family=None))
    assert p["role_family"] == "DATA_ENG"


def test_create_demand_runs_matching(conn, lead):
    d = demands.create(conn, lead, de_payload())
    assert d["status"] == "Open" and d["match_stats"]["eligible"] > 0
    m = matching.list_matches(conn, d["demand_id"])
    assert m["candidates"] and all(x["status"] == "suggested" for x in m["candidates"])
    assert "Must have: PySpark (level 3+), SQL (level 3+)" in d["jd_text"]


def test_pipeline_and_release_approval(conn, lead):
    d = demands.create(conn, lead, de_payload(positions=1, needed_by="2027-02-15"))
    did = d["demand_id"]
    cands = matching.list_matches(conn, did)["candidates"]
    bench = next(x for x in cands if not x["requires_approval"])
    alloc = next((x for x in cands if x["requires_approval"]), None)

    assert matching.act(conn, did, bench["emp_id"], "shortlist", lead)["status"] == "shortlisted"
    assert matching.act(conn, did, bench["emp_id"], "nominate", lead)["status"] == "nominated"
    with pytest.raises(BadRequest):
        matching.act(conn, did, bench["emp_id"], "shortlist", lead)

    if alloc:
        assert matching.act(conn, did, alloc["emp_id"], "nominate", lead)["status"] == "release_requested"
        approver = get_user(conn, alloc["approver_id"])
        stranger = get_user(conn, one(conn, "SELECT user_id FROM users WHERE app_role='employee' LIMIT 1")["user_id"])
        with pytest.raises(Forbidden):
            matching.decide_release(conn, did, alloc["emp_id"], stranger, True)
        assert any(p["emp_id"] == alloc["emp_id"] for p in matching.pending_approvals(conn, approver))
        assert matching.decide_release(conn, did, alloc["emp_id"], approver, True)["status"] == "release_approved"

    matching.act(conn, did, bench["emp_id"], "select", lead)
    assert demands.get(conn, did)["status"] == "Fulfilled - Internal"
    ev = rows(conn, "SELECT action FROM match_events WHERE demand_id=? ORDER BY id", (did,))
    assert [e["action"] for e in ev][:2] == ["shortlist", "nominate"]


def test_rerun_keeps_pipeline_status(conn, lead):
    d = demands.create(conn, lead, de_payload())
    did = d["demand_id"]
    first = matching.list_matches(conn, did)["candidates"][0]
    matching.act(conn, did, first["emp_id"], "shortlist", lead)
    matching.run_and_store(conn, did, lead["user_id"])
    again = {c["emp_id"]: c for c in matching.list_matches(conn, did)["candidates"]}
    assert again[first["emp_id"]]["status"] == "shortlisted"


def test_other_pipelines_flag(conn, lead):
    d1 = demands.create(conn, lead, de_payload())
    c = matching.list_matches(conn, d1["demand_id"])["candidates"][0]
    matching.act(conn, d1["demand_id"], c["emp_id"], "shortlist", lead)
    d2 = demands.create(conn, lead, de_payload())
    again = {x["emp_id"]: x for x in matching.list_matches(conn, d2["demand_id"])["candidates"]}
    if c["emp_id"] in again:
        assert "in_other_pipelines" in again[c["emp_id"]]["flags"]


def test_existing_generated_demand(conn):
    did = one(conn, "SELECT demand_id FROM demands WHERE status='Open' ORDER BY demand_id LIMIT 1")["demand_id"]
    res = matching.run_and_store(conn, did)
    assert res["stats"]["pool_size"] > 0
