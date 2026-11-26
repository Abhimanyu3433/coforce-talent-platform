"""Backend settings (override with environment variables)."""
import os
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]          # coforce/


ENV_FILE = PROJECT_ROOT / "backend" / ".env"


def _load_env_file(path: Path = ENV_FILE, override: bool = False):
    """Minimal .env reader (KEY=value lines). Real environment variables take precedence unless override=True."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.split(" #")[0].strip().strip('"').strip("'")
        if not v:                       # empty placeholder lines (e.g. "GEMINI_API_KEY=") never hide a real value
            continue
        if override or not os.environ.get(k):
            os.environ[k] = v
        else:
            os.environ.setdefault(k, v)


def reload_env_file():
    """Pick up edits to backend/.env without a restart (used by the chat status reload endpoint)."""
    _load_env_file(ENV_FILE, override=True)      # looked up at call time, so tests can point it elsewhere


_load_env_file()
DB_PATH = os.getenv("COFORCE_DB", str(PROJECT_ROOT / "data" / "coforce.db"))

JWT_SECRET = os.getenv("COFORCE_JWT_SECRET", "dev-only-secret-change-me-before-any-deployment")
JWT_ALGORITHM = "HS256"
TOKEN_MINUTES = int(os.getenv("COFORCE_TOKEN_MINUTES", "480"))

# Demo mode: any user without a password yet can sign in with DEMO_PASSWORD (it is then hashed and stored)
DEMO_MODE = os.getenv("COFORCE_DEMO_MODE", "1") == "1"
DEMO_PASSWORD = os.getenv("COFORCE_DEMO_PASSWORD", "Coforce@123")

# The synthetic data is generated "as of" 2026-11-25. Set COFORCE_AS_OF="" to use the real current date.
AS_OF_DATE = os.getenv("COFORCE_AS_OF", "2026-11-25")
RESIDENCY_GAP_DAYS = 30          # stints on the same project/account separated by <= 30 days count as continuous

CORS_ORIGINS = os.getenv("COFORCE_CORS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

PRIVILEGED_ROLES = {"lead", "manager", "hr"}


def as_of() -> date:
    return date.fromisoformat(AS_OF_DATE) if AS_OF_DATE else date.today()
