"""Upskilling advisor: recommendation quality, learning paths, self-enrollment, bench overview."""
import shutil

import pytest

from app import config
from app.db import connect, one, rows
from app.migrations import ensure_schema
from app.services import matching, upskilling
from app.services.errors import BadRequest


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db = tmp_path_factory.mktemp("up") / "coforce.db"
    shutil.copy(config.DB_PATH, db)
    c = connect(str(db))
    ensure_schema(c, refresh_residency=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def ctx(conn):
    return upskilling.Context(conn)


def bench_ids(conn, n=15):
    return [r["emp_id"] for r in rows(conn, "SELECT emp_id FROM employees WHERE status='bench' ORDER BY emp_id LIMIT ?", (n,))]


def test_recommendations_are_new_ranked_and_explained(conn, ctx):
    for eid in bench_ids(conn):
        person = upskilling.load_person(conn, eid)
        res = upskilling.recommend(ctx, person)
        recs = res["recommendations"]
        assert len(recs) <= 5
        scores = [r["score"] for r in recs if not r["already_learning"]]
        assert scores == sorted(scores, reverse=True)
        for r in recs:
            assert person["skills"].get(r["skill_id"], (0,))[0] < upskilling.TARGET_LEVEL   # never something they already have
            assert r["reasons"] and r["path"] and r["path"][-1]["skill_id"] == r["skill_id"]
            assert r["training_days"] == sum(p["days"] for p in r["path"])
            assert abs(sum(r["components"].values()) - r["score"]) < 0.3


def test_unlock_is_real(conn, ctx):
    """If the advisor says a skill unlocks a full match, the matching engine must agree once the skill is added."""
    checked = 0
    for eid in bench_ids(conn, 40):
        person = upskilling.load_person(conn, eid)
        for r in upskilling.recommend(ctx, person)["recommendations"]:
            for d in r["demands"]:
                if not d["full_match"]:
                    continue
                spec = next(s for s in ctx.specs if s["demand_id"] == d["demand_id"])
                boosted = dict(person, skills={**person["skills"], r["skill_id"]: (3, ctx.today.isoformat(), False)})
                res, why = matching.score_candidate(boosted, spec, ctx.ref, ctx.today)
                assert res and res["mandatory_met"] == res["mandatory_total"], (eid, r["skill"], d["demand_id"], why)
                checked += 1
    assert checked > 0


def test_learning_path_orders_prerequisites(conn, ctx):
    pyspark = one(conn, "SELECT skill_id FROM skills WHERE name='PySpark'")["skill_id"]
    eid = one(conn, """SELECT e.emp_id FROM employees e WHERE e.status='bench' AND e.role_family='QA'
                       AND NOT EXISTS (SELECT 1 FROM employee_skills x JOIN skills s USING(skill_id)
                                       WHERE x.emp_id=e.emp_id AND s.name IN ('Python','Apache Spark','PySpark','SQL'))
                       LIMIT 1""")["emp_id"]
    path = upskilling.learning_path(ctx, upskilling.load_person(conn, eid), pyspark)
    names = [p["skill"] for p in path]
    assert names[-1] == "PySpark" and {"Python", "Apache Spark"} <= set(names)
    assert names.index("Python") < names.index("PySpark") and names.index("Apache Spark") < names.index("PySpark")


def test_start_learning_and_no_duplicates(conn, ctx):
    eid = bench_ids(conn, 1)[0]
    rec = upskilling.recommend(ctx, upskilling.load_person(conn, eid))["recommendations"][0]
    out = upskilling.start_learning(conn, eid, rec["skill_id"])
    assert len(out["enrollments"]) == len(rec["path"]) and out["ready_by"]
    ends = [e["end_date"] for e in out["enrollments"]]
    starts = [e["start_date"] for e in out["enrollments"]]
    assert all(s > e for s, e in zip(starts[1:], ends[:-1]))            # sequential
    with pytest.raises(BadRequest):
        upskilling.start_learning(conn, eid, rec["skill_id"])
    again = upskilling.recommend(upskilling.Context(conn), upskilling.load_person(conn, eid))["recommendations"]
    assert all(r["already_learning"] for r in again if r["skill_id"] == rec["skill_id"])


def test_bench_overview(conn):
    upskilling.refresh_bench(conn)
    ov = upskilling.bench_overview(conn, ibu_id="BFS", page_size=10)
    assert ov["total"] > 0 and ov["people"] and ov["top_skills"]
    assert all(len(p["recommendations"]) <= 3 for p in ov["people"])
    aging = [p["bench_aging_days"] for p in ov["people"]]
    assert aging == sorted(aging, reverse=True)


def test_corporate_staff_get_nothing(conn, ctx):
    hr = one(conn, "SELECT emp_id FROM employees WHERE function='Corporate' LIMIT 1")["emp_id"]
    assert upskilling.recommend(ctx, upskilling.load_person(conn, hr))["recommendations"] == []
