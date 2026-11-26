"""Resume upload → suggested skills, and certificate proof verified by HR (real PDFs/DOCX built in memory)."""
import base64
import io
import shutil
import zipfile

import pytest

pytest.importorskip("pypdf")

from app import config  # noqa: E402
from app.db import connect, one, rows  # noqa: E402
from app.migrations import ensure_schema  # noqa: E402
from app.services import documents, employees, resume  # noqa: E402
from app.services.errors import BadRequest, Conflict, Forbidden, NotFound  # noqa: E402
from tests.test_jd_pdf import make_pdf  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
b64 = lambda raw: base64.b64encode(raw).decode()  # noqa: E731


@pytest.fixture(scope="module")
def conn(tmp_path_factory, monkeypatch_module):
    d = tmp_path_factory.mktemp("resume")
    shutil.copy(config.DB_PATH, d / "coforce.db")
    monkeypatch_module.setenv("COFORCE_UPLOADS", str(d / "uploads"))
    c = connect(str(d / "coforce.db"))
    ensure_schema(c)
    yield c
    c.close()


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def emp(conn):
    """An employee with a project-backed Python skill and at least one self-declared skill."""
    e = one(conn, """SELECT e.emp_id, e.first_name, e.last_name FROM employees e
                     JOIN employee_skills es USING(emp_id) JOIN skills s USING(skill_id)
                     WHERE s.name='Python' AND es.source='project'
                       AND e.emp_id IN (SELECT emp_id FROM employee_skills WHERE source='self_declared')
                     ORDER BY e.emp_id LIMIT 1""")
    e["self_declared"] = rows(conn, """SELECT s.skill_id, s.name, es.proficiency FROM employee_skills es JOIN skills s USING(skill_id)
                                       WHERE es.emp_id=? AND es.source='self_declared' ORDER BY s.name""", (e["emp_id"],))[0]
    return e


def make_docx(lines):
    body = "".join(f"<w:p><w:r><w:t>{l}</w:t></w:r></w:p>" for l in lines)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


# ---------------------------------------------------------------- resume
def test_resume_suggestions(conn, emp):
    sd = emp["self_declared"]
    pdf = make_pdf([f"{emp['first_name']} {emp['last_name']}", "Data Engineer with 6 years of total experience",
                    f"Skills: Python (expert), {sd['name']} (7 years), dbt, Apache Kafka (basic)",
                    "Built pipelines in Databricks; scheduled jobs with Airflow",
                    "Certifications: Microsoft Certified: Azure Data Engineer Associate (DP-203)"])
    r = resume.parse(conn, emp["emp_id"], "C:\\docs\\my cv.pdf", b64(pdf))
    assert r["file"]["name"] == "my cv.pdf" and r["name_found"] and r["resume_exp_years"] == 6
    by = {s["name"]: s for s in r["skills"]}
    assert by["Python"]["status"] == "on_profile" and by["Python"]["locked"]          # project skill: not overwritten
    assert by[sd["name"]]["status"] == "update" and by[sd["name"]]["years_used"] == 7
    assert by["dbt"]["status"] == "new"
    assert by["Apache Kafka"]["suggested_level"] == 2 and by["Apache Kafka"]["level_from"] == "resume"
    assert {"Databricks", "Apache Airflow"} <= set(by)
    assert any("DP-203" in c["name"] for c in r["certifications"])
    assert r["skills"][0]["status"] == "new"                                          # new ones listed first


def test_resume_apply(conn, emp):
    idx = {s["name"]: s["skill_id"] for s in rows(conn, "SELECT skill_id, name FROM skills")}
    sd = emp["self_declared"]
    out = resume.apply(conn, emp["emp_id"], [
        {"skill_id": idx["dbt"], "proficiency": 3, "years_used": 1.5},
        {"skill_id": sd["skill_id"], "proficiency": 4, "years_used": 7},
        {"skill_id": idx["Python"], "proficiency": 5, "years_used": None}])
    assert out["added"] == ["dbt"] and out["updated"] == [sd["name"]]
    assert out["skipped"][0]["name"] == "Python" and "project" in out["skipped"][0]["reason"]
    row = one(conn, "SELECT proficiency, source, verified FROM employee_skills WHERE emp_id=? AND skill_id=?",
              (emp["emp_id"], idx["dbt"]))
    assert row == {"proficiency": 3, "source": "self_declared", "verified": 0}
    with pytest.raises(BadRequest):
        resume.apply(conn, emp["emp_id"], [])


def test_resume_docx_and_bad_files(conn, emp):
    r = resume.parse(conn, emp["emp_id"], "cv.docx", b64(make_docx(["Profile summary for a cloud engineer role",
                                                                     "Skills: Terraform, Kubernetes, Python"])))
    assert r["file"]["kind"] == "docx" and {"Terraform", "Kubernetes"} <= {s["name"] for s in r["skills"]}
    assert not r["name_found"]
    with pytest.raises(BadRequest, match="PDF or Word"):
        resume.parse(conn, emp["emp_id"], "cv.png", b64(PNG))
    with pytest.raises(BadRequest, match="scanned"):
        resume.parse(conn, emp["emp_id"], "cv.pdf", b64(make_pdf(["hi"])))
    with pytest.raises(BadRequest, match="too large"):
        documents.decode(b64(b"%PDF" + b"0" * (documents.MAX_BYTES + 1)))


# ---------------------------------------------------------------- certificate proof
def hr_id(conn):
    return one(conn, "SELECT user_id FROM users WHERE app_role='hr' ORDER BY user_id LIMIT 1")["user_id"]


def test_proof_checks_and_access(conn, emp):
    proof = make_pdf([f"This certifies that {emp['first_name']} {emp['last_name']}",
                      "has earned the SnowPro Core Certification", "Snowflake Inc.", "Credential ID: SNO 4410"])
    c = employees.add_certification(conn, emp["emp_id"], "SnowPro Core Certification", "Snowflake", ["snowflake"],
                                    "2026-10-01", None, "SNO-4410", "../../etc/snow pro.pdf", b64(proof))
    assert c["has_proof"] and not c["needs_proof"] and "proof_file" not in c
    assert all(x["ok"] for x in c["proof_checks"])
    assert employees.list_certifications(conn, "pending")[0]["cert_id"] == c["cert_id"]   # with proof → top of queue
    data, media, name = employees.get_proof(conn, {"user_id": hr_id(conn), "app_role": "hr"}, c["cert_id"])
    assert data == proof and media == "application/pdf" and name == "snow pro.pdf"
    assert employees.get_proof(conn, {"user_id": emp["emp_id"], "app_role": "employee"}, c["cert_id"])[0] == proof
    with pytest.raises(Forbidden):
        employees.get_proof(conn, {"user_id": "CF100001", "app_role": "lead"}, c["cert_id"])
    wrong = make_pdf(["Certificate awarded to Somebody Else", "AWS Certified Cloud Practitioner"])
    c2 = employees.add_certification(conn, emp["emp_id"], "SnowPro Core Certification", "Snowflake", ["snowflake"],
                                     "2026-10-01", None, "SNO-9", "x.pdf", b64(wrong))
    checks = {x["check"]: x["ok"] for x in c2["proof_checks"]}
    assert checks == {"Employee name": False, "Credential ID": False, "Certification title": False, "Issuer": False}


def test_review_rules_and_resubmit(conn, emp):
    hr = hr_id(conn)
    c = employees.add_certification(conn, emp["emp_id"], "dbt Analytics Engineering Certification", "dbt Labs", ["dbt"], "2026-09-01")
    assert c["needs_proof"]
    with pytest.raises(BadRequest, match="No proof"):
        employees.review_certification(conn, c["cert_id"], hr, True)
    with pytest.raises(BadRequest, match="note"):
        employees.review_certification(conn, c["cert_id"], hr, False)
    employees.review_certification(conn, c["cert_id"], hr, False, "Please upload the certificate")
    with pytest.raises(Conflict):
        employees.review_certification(conn, c["cert_id"], hr, True)
    with pytest.raises(BadRequest, match="PDF, PNG image or JPG image"):
        employees.attach_proof(conn, emp["emp_id"], c["cert_id"], "a.txt", b64(b"just some text"))
    with pytest.raises(NotFound):
        employees.attach_proof(conn, "CF100001" if emp["emp_id"] != "CF100001" else "CF100002", c["cert_id"], "a.png", b64(PNG))
    r = employees.attach_proof(conn, emp["emp_id"], c["cert_id"], "dbt.png", b64(PNG))
    assert r["verification_status"] == "pending" and "Re-submitted" in r["review_note"]
    assert r["proof_checks"][0]["ok"] is None                                         # image → HR checks by eye
    v = employees.review_certification(conn, c["cert_id"], hr, True, "Matches the dbt credential page")
    assert v["verification_status"] == "verified"
    with pytest.raises(Conflict):
        employees.attach_proof(conn, emp["emp_id"], c["cert_id"], "dbt.png", b64(PNG))
    assert one(conn, """SELECT verified FROM employee_skills es JOIN skills s USING(skill_id)
                        WHERE es.emp_id=? AND s.name='dbt'""", (emp["emp_id"],))["verified"] == 1


def test_uploads_live_next_to_the_database(tmp_path, monkeypatch):
    monkeypatch.delenv("COFORCE_UPLOADS", raising=False)
    c = connect(str(tmp_path / "x.db"))
    assert documents.upload_root(c) == (tmp_path / "uploads").resolve()
    rel = documents.save(c, "certs/CE1", "a.png", PNG)
    assert documents.load(c, rel) == PNG
    with pytest.raises(NotFound):
        documents.load(c, "../x.db")
    c.close()


def test_legacy_certificate_still_reviewable(conn):
    leg = one(conn, "SELECT cert_id FROM certifications WHERE verification_status='pending' AND created_on IS NULL LIMIT 1")
    listed = next(x for x in employees.list_certifications(conn, "pending", limit=1000) if x["cert_id"] == leg["cert_id"])
    assert listed["legacy"] and not listed["needs_proof"]
    assert employees.review_certification(conn, leg["cert_id"], hr_id(conn), True)["verification_status"] == "verified"


def test_resume_ignores_overall_experience_and_cert_vendors(conn, emp):
    pdf = make_pdf(["Developer with 9 years of total experience building React apps",
                    "Frameworks: Spring Boot (4 years), React (3 years)",
                    "Certifications: Oracle Certified Professional: Java SE Developer"])
    r = resume.parse(conn, emp["emp_id"], "cv.pdf", b64(pdf))
    by = {s["name"]: s for s in r["skills"]}
    assert by["React"]["years_used"] == 3 and r["resume_exp_years"] == 9
    assert "Oracle Database" not in by
    assert [c["name"] for c in r["certifications"]] == ["Oracle Certified Professional: Java SE Developer"]
