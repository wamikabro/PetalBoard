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


# Changes on top of schema.sql. schema.sql is version 2 (it has steps up to 2 and sets version 2);
# later steps run on new and old databases alike. Add new steps at the end, never edit old ones.
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

# Tables copied to phones for offline use (GET /sync). Every insert or update gives the row a new
# number (rev) from one sequence; every delete is written to `deletions` with its own number.
# A phone asks for everything with a number above the last one it saw.
SYNC_TABLES = ["shop_settings", "users", "flowers", "suppliers", "investors", "bills", "bill_days", "kharcha",
               "advances", "khaata_shares", "investor_entries", "khaatas"]

MIGRATIONS[3] = """
    CREATE SEQUENCE change_seq;
    ALTER TABLE schema_meta ADD COLUMN db_id uuid NOT NULL DEFAULT gen_random_uuid();
    CREATE TABLE deletions (
        rev        bigint PRIMARY KEY DEFAULT nextval('change_seq'),
        tbl        text NOT NULL,
        row_key    text NOT NULL,
        deleted_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE FUNCTION bump_rev() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN NEW.rev := nextval('change_seq'); RETURN NEW; END $$;
    CREATE FUNCTION log_delete() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE r jsonb := to_jsonb(OLD);
    BEGIN
        INSERT INTO deletions (tbl, row_key) VALUES (TG_TABLE_NAME,
            CASE WHEN TG_TABLE_NAME = 'bill_days' THEN (r->>'bill_id') || ':' || (r->>'day') ELSE r->>'id' END);
        RETURN OLD;
    END $$;
""" + "".join(f"""
    ALTER TABLE {t} ADD COLUMN rev bigint NOT NULL DEFAULT nextval('change_seq');
    CREATE INDEX {t}_rev ON {t}(rev);
    CREATE TRIGGER {t}_rev BEFORE INSERT OR UPDATE ON {t} FOR EACH ROW EXECUTE FUNCTION bump_rev();
    CREATE TRIGGER {t}_deleted AFTER DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION log_delete();
""" for t in SYNC_TABLES)


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

# A phone's offline outbox sends each kharcha with its own id, so a kharcha sent twice is saved once.
MIGRATIONS[4] = """
    ALTER TABLE kharcha ADD COLUMN client_id text;
    CREATE UNIQUE INDEX kharcha_client ON kharcha(client_id) WHERE client_id IS NOT NULL;
    -- When the change to a day was made (on a phone it can be hours before it reaches the server).
    -- Two changes to one day: the one made later wins.
    ALTER TABLE bill_days ADD COLUMN made_at timestamptz;
"""
