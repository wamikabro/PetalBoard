"""A copy of the data for phones, so the app keeps working (and the data stays safe) without the server.

    GET /sync?since=<rev>    every row added or changed after `since`, every row deleted after it,
                             the row count of each table and the id of this database.

The phone only adds or updates rows from a reply, and deletes rows only from the `deletions` list,
so an error or an empty reply can never wipe what is saved on it. See web/src/lib/offline.ts.
"""

from fastapi import APIRouter, Depends, Query

import db
from security import current_user

router = APIRouter(tags=["sync"])

# Columns never sent to phones.
HIDDEN = {"users": {"password_hash"}}


def _key(table: str, row: dict) -> str:
    return f"{row['bill_id']}:{row['day']}" if table == "bill_days" else str(row["id"])


@router.get("/sync")
def sync(since: int = Query(default=0, ge=0), user: dict = Depends(current_user)):
    with db.connect() as conn:
        # One consistent picture of the database for the whole reply.
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        meta = conn.execute("SELECT db_id, version FROM schema_meta").fetchone()
        tables, counts, top = {}, {}, since
        for t in db.SYNC_TABLES:
            rows = conn.execute(f"SELECT * FROM {t} WHERE rev > %s ORDER BY rev", (since,)).fetchall()
            hide = HIDDEN.get(t, set())
            out = []
            for r in rows:
                top = max(top, r["rev"])
                r = {k: v for k, v in r.items() if k not in hide}
                r["_key"] = _key(t, r)
                out.append(r)
            tables[t] = out
            counts[t] = conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
        deletions = conn.execute(
            "SELECT rev, tbl, row_key FROM deletions WHERE rev > %s ORDER BY rev", (since,)).fetchall()
        if deletions:
            top = max(top, deletions[-1]["rev"])
        conn.execute("COMMIT")
    return {
        "db_id": str(meta["db_id"]),
        "schema_version": meta["version"],
        "since": since,
        "rev": top,
        "tables": tables,
        "deletions": [{"rev": d["rev"], "table": d["tbl"], "key": d["row_key"]} for d in deletions],
        "counts": counts,
    }
