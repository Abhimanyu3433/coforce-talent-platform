"""Uploaded documents: decode, detect type, extract text (PDF via pypdf, DOCX via the standard library), store safely.

Used by resume parsing and certification proofs. Files live under  <folder of the database>/uploads/  (data/ is
git-ignored), or COFORCE_UPLOADS if set.
"""
from __future__ import annotations

import base64
import binascii
import html
import io
import os
import re
import zipfile
from pathlib import Path

from .. import config
from .chat import jd_file
from .errors import BadRequest, NotFound

MAX_BYTES = 5 * 1024 * 1024
MAX_CHARS = 40_000

KINDS = {   # kind → (media type, file extension)
    "pdf": ("application/pdf", ".pdf"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "png": ("image/png", ".png"),
    "jpg": ("image/jpeg", ".jpg"),
}
LABEL = {"pdf": "PDF", "docx": "Word (.docx)", "png": "PNG image", "jpg": "JPG image"}


def decode(data_base64: str) -> bytes:
    if "," in data_base64[:100] and data_base64.lstrip().startswith("data:"):
        data_base64 = data_base64.split(",", 1)[1]              # tolerate a full data: URL
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise BadRequest("The file could not be read (invalid encoding).")
    if len(raw) > MAX_BYTES:
        raise BadRequest(f"The file is too large ({len(raw) / 1_048_576:.1f} MB). The limit is {MAX_BYTES // 1_048_576} MB.")
    if not raw:
        raise BadRequest("The file is empty.")
    return raw


def detect(data: bytes) -> str | None:
    head = data[:8]
    if data.lstrip()[:5].startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            return None
    return None


def require_kind(data: bytes, allowed: tuple[str, ...], what: str) -> str:
    kind = detect(data)
    if kind not in allowed:
        names = [LABEL[k] for k in allowed]
        raise BadRequest(f"Upload the {what} as {', '.join(names[:-1]) + ' or ' + names[-1] if len(names) > 1 else names[0]}"
                         + (" — that file is a " + LABEL[kind] if kind else "") + ".")
    return kind


def docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    except (zipfile.BadZipFile, KeyError):
        raise BadRequest("The Word file could not be read. Re-save it as .docx or PDF.")
    xml = re.sub(r"<w:tab/>", "\t", xml)
    xml = re.sub(r"</w:p>|<w:br/>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def extract_text(data: bytes, kind: str) -> dict:
    """{'text', 'pages', 'chars', 'truncated'}; raises BadRequest when no text can be read."""
    if kind == "pdf":
        try:
            info = jd_file.extract_pdf_text(data)                  # handles encrypted / scanned PDFs
        except BadRequest as e:
            msg = str(e.detail)
            if "scanned" in msg:
                msg = "No readable text was found in this PDF — it is probably a scanned image. Upload a text PDF or a Word file."
            elif "password" in msg:
                msg = "The PDF is password-protected. Remove the password and upload it again."
            raise BadRequest(msg)
        if info["truncated"]:                                     # JD limit is 15k; allow longer documents here
            import pypdf
            full = jd_file.clean("\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(data)).pages))
            info = {"text": full[:MAX_CHARS], "pages": info["pages"], "chars": len(full), "truncated": len(full) > MAX_CHARS}
        return info
    if kind == "docx":
        text = jd_file.clean(docx_text(data))
        if len(text) < jd_file.MIN_CHARS:
            raise BadRequest("No readable text was found in this Word file.")
        return {"text": text[:MAX_CHARS], "pages": None, "chars": len(text), "truncated": len(text) > MAX_CHARS}
    raise BadRequest("Text can only be read from PDF or Word files.")


# ------------------------------------------------------------------ storage
def upload_root(conn=None) -> Path:
    """Next to the database in use (so a test database copy keeps its uploads in its own folder)."""
    if os.getenv("COFORCE_UPLOADS"):
        return Path(os.getenv("COFORCE_UPLOADS"))
    db_file = conn.execute("PRAGMA database_list").fetchone()[2] if conn is not None else ""
    return Path(db_file or config.DB_PATH).resolve().parent / "uploads"


def safe_name(filename: str | None, kind: str) -> str:
    base = (filename or "").replace("\\", "/").split("/")[-1]
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", base.rsplit(".", 1)[0]).strip(" ._")[:80] or "document"
    return stem + KINDS[kind][1]


def save(conn, folder: str, name: str, data: bytes) -> str:
    """Write the file and return its path relative to the upload root (stored in the database)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*", folder):
        raise ValueError("bad folder")
    target = upload_root(conn) / folder
    target.mkdir(parents=True, exist_ok=True)
    for old in target.iterdir():                                    # one current file per folder
        if old.is_file():
            old.unlink()
    (target / name).write_bytes(data)
    return f"{folder}/{name}"


def load(conn, rel_path: str) -> bytes:
    root = upload_root(conn).resolve()
    p = (root / rel_path).resolve()
    if root not in p.parents or not p.is_file():
        raise NotFound("The file is no longer on the server.")
    return p.read_bytes()
