"""Idempotent schema additions on top of the generated database."""
from .db import columns, scalar
from .services import residency

ADD_COLUMNS = {
    "users": {"password_hash": "TEXT", "last_login": "TEXT"},
    "certifications": {"verified_by": "TEXT", "verified_on": "TEXT", "review_note": "TEXT", "created_on": "TEXT"},
    "employee_skills": {"updated_on": "TEXT"},
    "demands": {"last_matched_on": "TEXT", "last_matched_by": "TEXT", "match_stats": "TEXT", "source": "TEXT"},
    "training_batches": {"plan_id": "TEXT"},
    "training_enrollments": {"plan_id": "TEXT"},
}

CREATE_TABLES = [
    """CREATE TABLE IF NOT EXISTS matches (
        demand_id TEXT NOT NULL, emp_id TEXT NOT NULL, rank INTEGER, score REAL, fit_label TEXT, result TEXT,
        availability_date TEXT, pool TEXT, requires_approval INTEGER DEFAULT 0, approver_id TEXT,
        status TEXT NOT NULL DEFAULT 'suggested', note TEXT, updated_by TEXT, updated_on TEXT, computed_on TEXT,
        PRIMARY KEY (demand_id, emp_id))""",
    """CREATE TABLE IF NOT EXISTS training_plans (
        plan_id TEXT PRIMARY KEY, plan_type TEXT, demand_id TEXT, skill_id TEXT, created_by TEXT, created_on TEXT,
        status TEXT, overprovision_factor REAL, target INTEGER, ready_count INTEGER, trainees_planned INTEGER,
        shortfall INTEGER, feasible INTEGER, plan TEXT, decided_by TEXT, decided_on TEXT, decision_note TEXT)""",
    """CREATE TABLE IF NOT EXISTS training_plan_trainees (
        plan_id TEXT NOT NULL, emp_id TEXT NOT NULL, ready_by TEXT, on_time INTEGER, PRIMARY KEY (plan_id, emp_id))""",
    "CREATE INDEX IF NOT EXISTS ix_tpt_emp ON training_plan_trainees(emp_id)",
    """CREATE TABLE IF NOT EXISTS upskill_recs (
        emp_id TEXT NOT NULL, rank INTEGER, skill_id TEXT, score REAL, unlock_full INTEGER, unlock_eligible INTEGER,
        training_days INTEGER, relevant_demands INTEGER, detail TEXT, computed_on TEXT, PRIMARY KEY (emp_id, rank))""",
    """CREATE TABLE IF NOT EXISTS job_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, triggered_by TEXT, started_on TEXT, finished_on TEXT,
        seconds REAL, status TEXT, detail TEXT)""",
    """CREATE TABLE IF NOT EXISTS chat_sessions (
        session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_on TEXT, updated_on TEXT, status TEXT,
        demand_id TEXT, state TEXT, engine TEXT)""",
    """CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT, content TEXT, meta TEXT, at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_chat_msg_session ON chat_messages(session_id)",
    "CREATE INDEX IF NOT EXISTS ix_chat_user ON chat_sessions(user_id, updated_on)",
    """CREATE TABLE IF NOT EXISTS match_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, demand_id TEXT, emp_id TEXT, action TEXT, actor_id TEXT, at TEXT, note TEXT)""",
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_alloc_emp ON allocations(emp_id)",
    "CREATE INDEX IF NOT EXISTS ix_alloc_account ON allocations(account_id)",
    "CREATE INDEX IF NOT EXISTS ix_es_emp ON employee_skills(emp_id)",
    "CREATE INDEX IF NOT EXISTS ix_es_skill ON employee_skills(skill_id, proficiency)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_es_emp_skill ON employee_skills(emp_id, skill_id)",
    "CREATE INDEX IF NOT EXISTS ix_cert_emp ON certifications(emp_id)",
    "CREATE INDEX IF NOT EXISTS ix_cert_status ON certifications(verification_status)",
    "CREATE INDEX IF NOT EXISTS ix_enr_emp ON training_enrollments(emp_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_emp_id ON employees(emp_id)",
    "CREATE INDEX IF NOT EXISTS ix_emp_email ON employees(email)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_id ON users(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_matches_status ON matches(status)",
    "CREATE INDEX IF NOT EXISTS ix_matches_emp ON matches(emp_id)",
    "CREATE INDEX IF NOT EXISTS ix_ds_demand ON demand_skills(demand_id)",
    "CREATE INDEX IF NOT EXISTS ix_demands_status ON demands(status)",
]


def ensure_schema(conn, refresh_residency: bool = False):
    for ddl in CREATE_TABLES:
        conn.execute(ddl)
    for table, cols in ADD_COLUMNS.items():
        existing = columns(conn, table)
        for col, typ in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    for ddl in INDEXES:
        conn.execute(ddl)
    conn.commit()
    has_snapshot = scalar(conn, "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='residency_snapshot'")
    if refresh_residency or not has_snapshot:
        residency.refresh(conn)
