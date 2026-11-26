from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..deps import get_db, privileged, require_roles
from ..services.chat import llm, service

router = APIRouter(prefix="/api/chat/demand", tags=["demand assistant"])


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=6000)


class JDFileIn(BaseModel):
    filename: str = Field(default="job-description.pdf", max_length=255)
    data_base64: str = Field(min_length=10, max_length=7_200_000, description="The PDF, base64-encoded (max 5 MB)")


@router.get("/status")
def chat_status(user=Depends(privileged)):
    """Which brain answers: Gemini (GEMINI_API_KEY set) or the built-in rule-based parser."""
    return service.status()


@router.post("/status/reload")
def reload_status(user=Depends(require_roles("manager", "hr"))):
    """Re-read GEMINI_API_KEY from the environment without restarting."""
    llm.reset()
    return service.status()


@router.get("/sessions")
def my_sessions(user=Depends(privileged), conn=Depends(get_db)):
    return service.list_sessions(conn, user)


@router.post("/sessions")
def start(user=Depends(privileged), conn=Depends(get_db)):
    return service.start(conn, user)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, user=Depends(privileged), conn=Depends(get_db)):
    return service.view(conn, session_id, user)


@router.post("/sessions/{session_id}/messages")
def post_message(session_id: str, body: MessageIn, user=Depends(privileged), conn=Depends(get_db)):
    return service.post(conn, user, session_id, body.text)


@router.post("/sessions/{session_id}/jd-pdf")
def upload_jd_pdf(session_id: str, body: JDFileIn, user=Depends(privileged), conn=Depends(get_db)):
    """Upload a job-description PDF: its text is read and handled like a pasted JD."""
    return service.post_jd_pdf(conn, user, session_id, body.filename, body.data_base64)
