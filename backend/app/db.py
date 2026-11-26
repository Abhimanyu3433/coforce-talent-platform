"""Thin sqlite3 helpers. All SQL lives in services/; swap this module to move to Postgres."""
import sqlite3

from . import config


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn, sql: str, params=()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def scalar(conn, sql: str, params=()):
    r = conn.execute(sql, params).fetchone()
    return r[0] if r else None


def columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
