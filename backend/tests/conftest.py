"""Test isolation: never use the developer's backend/.env (real Gemini key) — tests run offline on the built-in parser."""
import os
from pathlib import Path

import pytest

from app import config
from app.services.chat import llm

SECRET_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


@pytest.fixture(autouse=True, scope="session")
def _no_real_gemini():
    saved = {k: os.environ.pop(k) for k in SECRET_VARS if k in os.environ}   # loaded from .env when app.config was imported
    real_env_file = config.ENV_FILE
    config.ENV_FILE = Path(os.devnull) / "no-such-.env"
    os.environ["COFORCE_NIGHTLY_AT"] = "off"
    llm.reset()
    yield
    config.ENV_FILE = real_env_file
    os.environ.update(saved)
    llm.reset()
