"""Demand assistant: job-description PDF upload (real PDFs built in memory, read with pypdf)."""
import base64
import shutil

import pytest

pytest.importorskip("pypdf")

from app import config  # noqa: E402
from app.db import connect, one  # noqa: E402
from app.migrations import ensure_schema  # noqa: E402
from app.services.auth import get_user  # noqa: E402
from app.services.chat import jd_file, service  # noqa: E402
from app.services.errors import BadRequest, Forbidden  # noqa: E402

JD = [
    "Job Title: Senior Data Engineer",
    "Client: Halion Financial",
    "No. of positions: 3",
    "Location: Pune, Hyderabad (Hybrid)",
    "Experience: 4-8 years",
    "Start date: 15 Jan 2027",
    "Duration: 12 months",
    "Mandatory skills: PySpark (advanced), SQL, Databricks, Azure Data Factory",
    "Good to have: Airflow, Snowflake",
    "Client interview required. Shift: UK overlap.",
]


def make_pdf(lines: list[str]) -> bytes:
    """Minimal valid one-page PDF with Helvetica text."""
    esc = lambda t: t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
    content = "BT /F1 11 Tf 50 780 Td 14 TL\n" + "".join(f"({esc(l)}) Tj T*\n" for l in lines) + "ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db = tmp_path_factory.mktemp("pdf") / "coforce.db"
    shutil.copy(config.DB_PATH, db)
    c = connect(str(db))
    ensure_schema(c, refresh_residency=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def lead(conn):
    return get_user(conn, one(conn, "SELECT user_id FROM users WHERE app_role='lead' ORDER BY user_id LIMIT 1")["user_id"])


def test_extract_text():
    info = jd_file.extract_pdf_text(make_pdf(JD))
    assert info["pages"] == 1 and not info["truncated"]
    assert "Senior Data Engineer" in info["text"] and "Azure Data Factory" in info["text"]


@pytest.mark.parametrize("data,msg", [
    (b"hello, this is not a pdf at all", "isn't a PDF"),
    (make_pdf([]), "No readable text"),
])
def test_bad_files(data, msg):
    with pytest.raises(BadRequest) as e:
        jd_file.extract_pdf_text(data)
    assert msg in str(e.value.detail)


def test_bad_base64_and_size(monkeypatch):
    with pytest.raises(BadRequest):
        jd_file.decode_base64("@@@not-base64@@@")
    monkeypatch.setattr(jd_file, "MAX_BYTES", 100)
    with pytest.raises(BadRequest) as e:
        jd_file.decode_base64(b64(make_pdf(JD)))
    assert "too large" in str(e.value.detail)
    assert jd_file.decode_base64("data:application/pdf;base64," + b64(b"%PDF-1.4 x")) == b"%PDF-1.4 x"


def test_upload_fills_the_draft(conn, lead):
    v = service.start(conn, lead)
    v = service.post_jd_pdf(conn, lead, v["session_id"], "C:\\Users\\me\\Desktop\\DE_JD.pdf", b64(make_pdf(JD)))
    up = next(m for m in v["messages"] if m["role"] == "user")
    assert up["attachment"]["name"] == "DE_JD.pdf" and up["attachment"]["pages"] == 1
    d = v["draft"]
    assert d["role_title"] == "Senior Data Engineer" and d["positions"] == 3
    assert d["account_name"] == "Halion Financial"
    assert {m["name"] for m in d["mandatory"]} == {"PySpark", "SQL", "Databricks", "Azure Data Factory"}
    assert next(m for m in d["mandatory"] if m["name"] == "PySpark")["min_proficiency"] == 4
    assert {n["name"] for n in d["nice"]} == {"Apache Airflow", "Snowflake"}
    assert set(d["locations"]) == {"Pune", "Hyderabad"} and d["work_mode"] == "Hybrid"
    assert (d["exp_min"], d["exp_max"]) == (4, 8)
    assert (d["needed_by"], d["duration_months"], d["shift"], d["client_interview"]) == ("2027-01-15", 12, "UK Overlap", True)
    assert v["supply"] is not None                                    # live supply check ran


def test_upload_respects_session_owner(conn, lead):
    v = service.start(conn, lead)
    other = get_user(conn, one(conn, "SELECT user_id FROM users WHERE app_role='manager' ORDER BY user_id LIMIT 1")["user_id"])
    with pytest.raises(Forbidden):
        service.post_jd_pdf(conn, other, v["session_id"], "x.pdf", b64(make_pdf(JD)))
