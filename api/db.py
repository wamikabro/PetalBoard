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


# Changes to a database made before them. schema.sql already has all of them and sets the
# latest version, so a new database skips this list. Add new steps at the end, never edit old ones.
MIGRATIONS: dict[int, str] = {
    2: """
    ALTER TABLE advances DROP CONSTRAINT IF EXISTS advances_kind_check;
    ALTER TABLE advances ADD CONSTRAINT advances_kind_check
        CHECK (kind IN ('given', 'recovered', 'bill_cut', 'opening', 'written_off'));
    CREATE TABLE khaatas (
        id             serial PRIMARY KEY,
        supplier_id    int NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
        opened_on      date NOT NULL,
        terms          text,
        commission_pct numeric(5,2) CHECK (commission_pct IS NULL OR (commission_pct >= 0 AND commission_pct <= 100)),
        closed_on      date CHECK (closed_on IS NULL OR closed_on >= opened_on),
        close_reason   text,
        written_off    bigint,
        ended_shares   jsonb,
        created_at     timestamptz NOT NULL DEFAULT now(),
        created_by     int REFERENCES users(id),
        closed_by      int REFERENCES users(id)
    );
    CREATE UNIQUE INDEX khaatas_one_open ON khaatas(supplier_id) WHERE closed_on IS NULL;
    """,
}


def migrate(url: str | None = None) -> None:
    """Create the schema if the database is empty, then apply any newer migrations. Safe on every start."""
    with connect(url) as conn:
        conn.autocommit = True  # schema.sql has its own BEGIN/COMMIT
        exists = conn.execute("SELECT to_regclass('public.schema_meta') AS t").fetchone()["t"]
        if not exists:
            conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        version = conn.execute("SELECT max(version) AS v FROM schema_meta").fetchone()["v"] or 1
        for v in sorted(k for k in MIGRATIONS if k > version):
            with conn.transaction():  # each step all or nothing
                conn.execute(MIGRATIONS[v])
                conn.execute("UPDATE schema_meta SET version = %s", (v,))
