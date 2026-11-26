"""Nightly maintenance (Stage 7).

    python -m app.jobs nightly        # run once from the command line (from coforce/backend)

Steps, in order:
  1. training   roll enrolments and batches forward by date (scheduled → in progress → completed); completed courses
                add/raise the skill on the profile (Beginner → level 2, Intermediate → 3, Advanced → 4, source 'training');
                bench people with an active course show as 'training' and back to 'bench' when it ends
  2. residency  recompute the residency snapshot
  3. matching   re-run matching for every open demand (pipeline statuses are kept)
  4. upskilling refresh the bench-wide recommendation snapshot
Each run is logged in `job_runs`. The app also runs this daily at COFORCE_NIGHTLY_AT (default 02:00, "off" disables).
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone

from . import config
from .db import connect, rows
from .migrations import ensure_schema
from .services import matching, residency, upskilling

log = logging.getLogger("coforce.jobs")
LEVEL_FOR_COURSE = {"Beginner": 2, "Intermediate": 3, "Advanced": 4}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def roll_training(conn) -> dict:
    today = config.as_of().isoformat()
    started = conn.execute("UPDATE training_enrollments SET status='in_progress' WHERE status='scheduled' AND start_date <= ?",
                           (today,)).rowcount
    done_rows = rows(conn, """SELECT t.enrollment_id, t.emp_id, t.skill_id, t.end_date, c.level FROM training_enrollments t
                              JOIN courses c USING(course_id) WHERE t.status='in_progress' AND t.end_date < ?""", (today,))
    for r in done_rows:
        lvl = LEVEL_FOR_COURSE.get(r["level"], 2)
        ex = conn.execute("SELECT proficiency FROM employee_skills WHERE emp_id=? AND skill_id=?", (r["emp_id"], r["skill_id"])).fetchone()
        if ex:
            conn.execute("""UPDATE employee_skills SET proficiency=max(proficiency, ?), last_used=?, updated_on=?,
                            source=CASE WHEN source IN ('self_declared','academic') THEN 'training' ELSE source END
                            WHERE emp_id=? AND skill_id=?""", (lvl, r["end_date"], _now(), r["emp_id"], r["skill_id"]))
        else:
            conn.execute("""INSERT INTO employee_skills (emp_id, skill_id, proficiency, years_used, first_used, last_used, source,
                            verified, updated_on) VALUES (?,?,?,0.1,?,?, 'training', 0, ?)""",
                         (r["emp_id"], r["skill_id"], lvl, r["end_date"], r["end_date"], _now()))
        conn.execute("UPDATE training_enrollments SET status='completed' WHERE enrollment_id=?", (r["enrollment_id"],))
    b_run = conn.execute("UPDATE training_batches SET status='Running' WHERE status='Scheduled' AND start_date <= ?", (today,)).rowcount
    b_done = conn.execute("UPDATE training_batches SET status='Completed' WHERE status='Running' AND end_date < ?", (today,)).rowcount
    to_training = conn.execute("""UPDATE employees SET status='training' WHERE status='bench' AND emp_id IN
                                  (SELECT emp_id FROM training_enrollments WHERE status='in_progress')""").rowcount
    to_bench = conn.execute("""UPDATE employees SET status='bench', bench_since=coalesce(bench_since, ?) WHERE status='training'
                               AND emp_id NOT IN (SELECT emp_id FROM training_enrollments WHERE status='in_progress')""",
                            (today,)).rowcount
    conn.commit()
    return {"enrolments_started": started, "enrolments_completed": len(done_rows), "batches_started": b_run,
            "batches_completed": b_done, "bench_to_training": to_training, "training_to_bench": to_bench}


def rematch_open(conn) -> dict:
    ids = [r["demand_id"] for r in rows(conn, "SELECT demand_id FROM demands WHERE status='Open'")]
    for did in ids:
        matching.run_and_store(conn, did)
    return {"open_demands_rematched": len(ids)}


STEPS = [("training", roll_training), ("residency", lambda c: residency.refresh(c)), ("matching", rematch_open),
         ("upskilling", upskilling.refresh_bench)]


def run_nightly(conn=None, triggered_by: str = "schedule") -> dict:
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        started = _now()
        t0 = time.time()
        detail, status = {}, "ok"
        for name, fn in STEPS:
            s = time.time()
            try:
                detail[name] = fn(conn) | {"seconds": round(time.time() - s, 1)}
            except Exception as e:  # keep going; record the failure
                log.exception("nightly step %s failed", name)
                detail[name] = {"error": f"{e.__class__.__name__}: {e}"}
                status = "partial"
        conn.execute("INSERT INTO job_runs (job, triggered_by, started_on, finished_on, seconds, status, detail) VALUES (?,?,?,?,?,?,?)",
                     ("nightly", triggered_by, started, _now(), round(time.time() - t0, 1), status, json.dumps(detail)))
        conn.commit()
        log.info("nightly job %s in %.1fs", status, time.time() - t0)
        return {"status": status, "started_on": started, "seconds": round(time.time() - t0, 1), "detail": detail}
    finally:
        if own:
            conn.close()


def recent_runs(conn, limit: int = 20) -> list[dict]:
    out = rows(conn, "SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,))
    for o in out:
        o["detail"] = json.loads(o["detail"] or "{}")
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 2 or sys.argv[1] != "nightly":
        print("usage: python -m app.jobs nightly")
        sys.exit(2)
    print(json.dumps(run_nightly(triggered_by="cli"), indent=2))
