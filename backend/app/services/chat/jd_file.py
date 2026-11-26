"""Job-description PDF → plain text for the demand assistant (uses pypdf: pip install pypdf)."""
from __future__ import annotations

import base64
import binascii
import io
import re

from ..errors import BadRequest

MAX_BYTES = 5 * 1024 * 1024        # 5 MB
MAX_CHARS = 15_000                 # longer JDs are cut (the important parts come first in practically every JD)
MIN_CHARS = 40


def decode_base64(data_base64: str) -> bytes:
    if "," in data_base64[:100] and data_base64.lstrip().startswith("data:"):
        data_base64 = data_base64.split(",", 1)[1]              # tolerate a full data: URL
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise BadRequest("The file could not be read (invalid encoding).")
    if len(raw) > MAX_BYTES:
        raise BadRequest(f"The PDF is too large ({len(raw) / 1_048_576:.1f} MB). The limit is {MAX_BYTES // 1_048_576} MB.")
    return raw


def clean(text: str) -> str:
    text = text.replace(" ", " ").replace("•", "-").replace("", "-").replace("●", "-")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)                  # words hyphenated across lines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_text(data: bytes) -> dict:
    try:
        import pypdf
    except ImportError:
        raise BadRequest("PDF upload needs the 'pypdf' package on the server: run  pip install pypdf  and restart the backend.")
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise BadRequest("That file isn't a PDF. Upload a .pdf job description, or paste the text into the chat.")
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise BadRequest("The PDF is password-protected. Remove the password or paste the text instead.")
        pages = [p.extract_text() or "" for p in reader.pages]
    except BadRequest:
        raise
    except Exception as e:
        raise BadRequest(f"The PDF could not be read ({e.__class__.__name__}). Try re-saving it, or paste the text instead.")
    text = clean("\n".join(pages))
    if len(text) < MIN_CHARS:
        raise BadRequest("No readable text was found in this PDF — it is probably a scanned image. "
                         "Paste the job description text into the chat instead.")
    truncated = len(text) > MAX_CHARS
    return {"text": text[:MAX_CHARS], "pages": len(pages), "chars": len(text), "truncated": truncated}
