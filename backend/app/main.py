"""Coforce Talent Platform API.

Run from coforce/backend:   uvicorn app.main:app --reload --port 8000
Docs:                       http://localhost:8000/docs
If frontend/dist exists (npm run build), the app is also served at http://localhost:8000/.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, jobs
from .db import connect, scalar
from .migrations import ensure_schema
from .routers import admin, auth, chat, demands, employees, reference, residency, training, upskilling
from .services.errors import ServiceError

logging.basicConfig(level=os.getenv("COFORCE_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("coforce")
NIGHTLY_AT = os.getenv("COFORCE_NIGHTLY_AT", "02:00")          # local time HH:MM, or "off"
DIST = config.PROJECT_ROOT / "frontend" / "dist"


async def _nightly_loop():
    hh, mm = (int(x) for x in NIGHTLY_AT.split(":"))
    while True:
        now = datetime.now()
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            await asyncio.to_thread(jobs.run_nightly, None, "schedule")
        except Exception:  # never let the loop die
            log.exception("scheduled nightly job failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not Path(config.DB_PATH).exists():
        raise RuntimeError(f"Database not found at {config.DB_PATH}. Generate it first: "
                           f"python -m data_generator.generate --out data   (from the coforce folder)")
    conn = connect()
    try:
        ensure_schema(conn, refresh_residency=True)    # recompute residency on every start
    finally:
        conn.close()
    task = None
    if NIGHTLY_AT.lower() != "off":
        task = asyncio.create_task(_nightly_loop())
        log.info("nightly job scheduled daily at %s", NIGHTLY_AT)
    yield
    if task:
        task.cancel()


app = FastAPI(title="Coforce Talent Platform", version="0.7.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


for r in (auth.router, employees.router, residency.router, reference.router, demands.router, chat.router,
          training.router, upskilling.router, admin.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    conn = connect()
    try:
        last = scalar(conn, "SELECT finished_on FROM job_runs ORDER BY id DESC LIMIT 1")
        emps = scalar(conn, "SELECT count(*) FROM employees")
    finally:
        conn.close()
    return {"status": "ok", "version": app.version, "as_of": config.as_of().isoformat(), "db": config.DB_PATH,
            "employees": emps, "nightly_at": NIGHTLY_AT, "last_nightly_run": last, "frontend_built": DIST.exists()}


# ---------------- serve the built React app (single-process deployment / Docker)
if (DIST / "index.html").exists():
    if (DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")
