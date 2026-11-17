"""Database connections and the one-time schema setup."""

from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

import config

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or config.DATABASE_URL, row_factory=dict_row)


@contextmanager
def transaction(user_id: int | None = None, url: str | None = None):
    """One database transaction. `user_id` is kept in the session setting app.user_id."""
    with connect(url) as conn:
        with conn.transaction():
            if user_id is not None:
                conn.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id),))
            yield conn


def migrate(url: str | None = None) -> None:
    """Create the schema if the database is empty. Safe to run on every start."""
    with connect(url) as conn:
        conn.autocommit = True  # schema.sql has its own BEGIN/COMMIT
        exists = conn.execute("SELECT to_regclass('public.schema_meta') AS t").fetchone()["t"]
        if exists:
            return
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
