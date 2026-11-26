"""Demand assistant: parsing, multi-turn conversations, Gemini merge path (with a fake model)."""
import shutil
from datetime import date

import pytest

from app import config
from app.db import connect, one
from app.migrations import ensure_schema
from app.services.auth import get_user
from app.services.chat import llm, rules, service
from app.services.chat.context import ChatContext
from app.services.errors import Forbidden

TODAY = date(2026, 11, 25)


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db = tmp_path_factory.mktemp("chat") / "coforce.db"
    shutil.copy(config.DB_PATH, db)
    c = connect(str(db))
    ensure_schema(c, refresh_residency=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def lead(conn):
    uid = one(conn, """SELECT u.user_id FROM users u JOIN employees e ON e.emp_id=u.user_id
                       WHERE u.app_role='lead' AND e.ibu_id='BFS' LIMIT 1""")["user_id"]
    return get_user(conn, uid)


@pytest.fixture(autouse=True)
def rules_mode(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    llm.reset()
    yield
    llm.reset()


def chat(conn, user, msgs):
    v = service.start(conn, user)
    for m in msgs:
        v = service.post(conn, user, v["session_id"], m)
    return v


# ---------------- parsing helpers
@pytest.mark.parametrize("text,expected", [
    ("by 15 jan", "2027-01-15"), ("15/01/2027", "2027-01-15"), ("in 30 days", "2026-12-25"), ("asap", "2026-12-09"),
    ("2027-02-01", "2027-02-01"), ("jan 10", "2027-01-10"), ("start of march", "2027-03-01"), ("in 2 weeks", "2026-12-09"),
])
def test_dates(text, expected):
    assert rules.parse_date(text, TODAY) == expected


def test_skill_list_split():
    assert rules.split_skill_list("PySpark 4, SQL (level 3) and Databricks") == [("PySpark", 4), ("SQL", 3), ("Databricks", None)]
    assert rules.split_skill_list("expert kafka; basic docker") == [("kafka", 5), ("docker", 2)]


def test_scan_ignores_generic_words(conn, lead):
    ctx = ChatContext(conn, lead)
    names = {ctx.skills.get(s)["name"] for s, _ in rules.scan_skills("We are hiring a resource with Java and k8s, rest of team on site", ctx.skills)}
    assert names == {"Java", "Kubernetes"}


# ---------------- conversations
def test_step_by_step_conversation(conn, lead):
    v = chat(conn, lead, ["I need 4 senior data engineers", "pyspark 4, sql, adf and databricks", "3", "airflow, snowflake",
                          "4-8 years", "Halion Financial", "none", "Pune, Hyderabad, remote ok", "hybrid", "by 15 jan",
                          "12 months", "US Overlap", "yes", "high", "1.5x"])
    d = v["draft"]
    assert d["role_title"] == "Senior Data Engineer" and d["positions"] == 4
    assert {m["name"]: m["min_proficiency"] for m in d["mandatory"]} == {"PySpark": 4, "SQL": 3, "Azure Data Factory": 3, "Databricks": 3}
    assert {n["name"] for n in d["nice"]} == {"Apache Airflow", "Snowflake"}
    assert (d["exp_min"], d["exp_max"], d["grade_min"], d["grade_max"]) == (4, 8, "A3", "B2")
    assert d["account_name"] == "Halion Financial" and set(d["locations"]) == {"Pune", "Hyderabad"} and d["remote_allowed"]
    assert (d["needed_by"], d["duration_months"], d["shift"], d["client_interview"], d["priority"], d["overprovision_factor"]) == \
           ("2027-01-15", 12, "US Overlap", True, "High", 1.5)
    assert v["phase"] == "confirm" and v["missing"] == [] and v["supply"]["needed"] == 6
    v = service.post(conn, lead, v["session_id"], "Create demand")
    assert v["phase"] == "created" and v["demand_id"].startswith("DM")
    dem = one(conn, "SELECT source, positions, overprovision_factor FROM demands WHERE demand_id=?", (v["demand_id"],))
    assert dem == {"source": "chatbot", "positions": 4, "overprovision_factor": 1.5}


def test_pasted_jd(conn, lead):
    jd = ("We are hiring for Halion Financial: need 6 Java developers with 4-7 years experience. Must have Java, Spring Boot "
          "(advanced), microservices and Kafka. Nice to have: React, Docker, AWS. Location Bangalore or Chennai, hybrid, "
          "client interview required. Need them by 10 Jan for 18 months, US overlap shift. High priority.")
    v = chat(conn, lead, [jd])
    d = v["draft"]
    assert d["positions"] == 6 and d["role_title"] == "Java Developer"
    assert {m["name"] for m in d["mandatory"]} == {"Java", "Spring Boot", "Microservices", "Apache Kafka"}
    assert next(m for m in d["mandatory"] if m["name"] == "Spring Boot")["min_proficiency"] == 4
    assert next(m for m in d["mandatory"] if m["name"] == "Microservices")["min_proficiency"] is None
    assert {n["name"] for n in d["nice"]} == {"React", "Docker", "AWS"}
    assert set(d["locations"]) == {"Bengaluru", "Chennai"} and d["work_mode"] == "Hybrid"
    assert (d["needed_by"], d["duration_months"], d["shift"], d["client_interview"], d["priority"]) == \
           ("2027-01-10", 18, "US Overlap", True, "High")
    assert v["missing"][0] == "skill_level"


def test_relax_remove_skip_and_early_create(conn, lead):
    v = chat(conn, lead, ["2 QA engineers", "selenium, java, cucumber, jmeter", "skip", "none", "skip", "none",
                          "make jmeter optional", "remove cucumber", "Noida", "in 30 days"])
    d = v["draft"]
    assert {m["name"] for m in d["mandatory"]} == {"Selenium", "Java"}
    assert {n["name"] for n in d["nice"]} == {"JMeter"}
    assert d["ibu_id"] == lead["ibu_id"] and d["exp_min"] == 2
    v = service.post(conn, lead, v["session_id"], "create it")          # optional fields default
    assert v["phase"] == "created"
    assert "Defaults used" in v["messages"][-1]["content"]


def test_confirm_blocked_until_required(conn, lead):
    v = chat(conn, lead, ["1 devops engineer", "create demand"])
    assert v["phase"] == "collect" and "still need" in v["messages"][-1]["content"]


def test_unknown_skill_and_start_over(conn, lead):
    v = chat(conn, lead, ["3 data scientists", "pythn, qwertyskill"])
    assert any(m["name"] == "Python" for m in v["draft"]["mandatory"])
    assert "couldn't match “qwertyskill”" in v["messages"][-1]["content"]
    v = service.post(conn, lead, v["session_id"], "start over")
    assert v["draft"]["mandatory"] == [] and v["phase"] == "collect"


def test_session_is_private(conn, lead):
    v = service.start(conn, lead)
    other = get_user(conn, one(conn, "SELECT user_id FROM users WHERE app_role='manager' LIMIT 1")["user_id"])
    with pytest.raises(Forbidden):
        service.post(conn, other, v["session_id"], "hi")


# ---------------- Gemini path with a fake model
class FakeGemini:
    def extract(self, text, asked, ctx, draft):
        return {"intent": "provide", "role_title": "Lead Data Engineer", "positions": 2,
                "mandatory_skills": [{"skill": "Databricks", "min_proficiency": 4}]}


def test_gemini_merge(conn, lead, monkeypatch):
    monkeypatch.setattr(llm, "_MODE", "gemini")
    monkeypatch.setattr(llm, "_EXTRACTOR", FakeGemini())
    v = chat(conn, lead, ["two leads for databricks work in Pune"])
    d = v["draft"]
    assert d["role_title"] == "Lead Data Engineer" and d["positions"] == 2      # from Gemini
    assert d["locations"] == ["Pune"]                                            # from rules (Gemini left it out)
    assert v["messages"][-1]["source"] == "gemini"


class BrokenGemini:
    def extract(self, *a):
        raise RuntimeError("quota exceeded")


def test_gemini_failure_falls_back(conn, lead, monkeypatch):
    monkeypatch.setattr(llm, "_MODE", "gemini")
    monkeypatch.setattr(llm, "_EXTRACTOR", BrokenGemini())
    v = chat(conn, lead, ["I need 3 java developers"])
    assert v["draft"]["positions"] == 3 and v["messages"][-1]["source"].startswith("rules")


def test_status_without_key():
    s = service.status()
    assert s["mode"] == "rules" and "GEMINI_API_KEY" in s["reason"]


def test_message_values_win_over_project_defaults(conn, lead):
    v = chat(conn, lead, ["2 data engineers", "pyspark, databricks", "3", "none", "3-6 years", "Halion Financial",
                          "Payments Data Platform Modernization, Pune only, UK shift"])
    d = v["draft"]
    assert d["project_name"] == "Payments Data Platform Modernization"
    assert d["locations"] == ["Pune"] and d["shift"] == "UK Overlap"
    note = next(m["content"] for m in reversed(v["messages"]) if m["role"] == "assistant")
    assert "Bhubaneswar" not in note and "UK" not in note.split("from the project")[0][-60:]


def test_empty_chats_are_reused_and_hidden(conn):
    u = get_user(conn, one(conn, "SELECT user_id FROM users WHERE app_role='manager' ORDER BY user_id LIMIT 1 OFFSET 3")["user_id"])
    a = service.start(conn, u)
    b = service.start(conn, u)
    assert a["session_id"] == b["session_id"]
    assert all(s["session_id"] != a["session_id"] for s in service.list_sessions(conn, u))
    service.post(conn, u, a["session_id"], "I need 2 java developers")
    assert any(s["session_id"] == a["session_id"] for s in service.list_sessions(conn, u))
    assert service.start(conn, u)["session_id"] != a["session_id"]


# ---------------- Gemini resilience: fallback models, auth errors, cooldown (no network: _call is faked)
class FakeErr(Exception):
    def __init__(self, code, msg):
        super().__init__(f"{code} {msg}")
        self.code = code


def fake_extractor(responses):
    ex = llm.GeminiExtractor.__new__(llm.GeminiExtractor)
    ex.models = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-3.5-flash"]
    ex.last_model = ex.models[0]
    ex.calls = []

    def _call(model, prompt):
        ex.calls.append(model)
        r = responses[len(ex.calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r
    ex._call = _call
    return ex


def test_gemini_falls_back_to_next_model(conn, lead):
    ex = fake_extractor([FakeErr(503, "UNAVAILABLE high demand"), '{"intent": "provide", "positions": 2}'])
    out = ex.extract("two please", "positions", ChatContext(conn, lead), {"mandatory": [], "nice": []})
    assert out["positions"] == 2 and ex.calls == ["gemini-3.8-flash", "gemini-flash-latest"]
    assert ex.last_model == "gemini-flash-latest"


def test_gemini_auth_error_stops_immediately(conn, lead):
    ex = fake_extractor([FakeErr(400, "API_KEY_INVALID")])
    with pytest.raises(FakeErr):
        ex.extract("x", None, ChatContext(conn, lead), {"mandatory": [], "nice": []})
    assert len(ex.calls) == 1


def test_gemini_outage_pauses_then_uses_rules(conn, lead, monkeypatch):
    ex = fake_extractor([FakeErr(503, "UNAVAILABLE")] * 3)
    monkeypatch.setattr(llm, "_MODE", "gemini")
    monkeypatch.setattr(llm, "_EXTRACTOR", ex)
    v = chat(conn, lead, ["I need 3 java developers"])
    assert v["draft"]["positions"] == 3 and v["messages"][-1]["source"] == "rules (Gemini error)"
    n = len(ex.calls)
    v = service.post(conn, lead, v["session_id"], "spring boot, java")
    assert len(ex.calls) == n                                  # paused: Gemini not called again
    assert "paused" in v["messages"][-1]["source"]
