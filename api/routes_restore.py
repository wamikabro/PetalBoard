"""Fill a new, empty server from a copy of the data: a phone's copy (Settings → "Download this device's copy",
a .json file) or the server backup zip (its _restore/ folder).

    POST /restore      owner only; the file as the request body

Safety: it only loads into a database that has no suppliers and no bills yet (a new server). It never
changes or mixes into existing data. Users are not restored (the copies hold no passwords): the owner
creates logins on the new server first; "entered by" links to people who do not exist there are left empty.
"""

import io
import json
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb

import db
from security import require

router = APIRouter(tags=["restore"])

# Parents before children. users are not restored.
ORDER = ["shop_settings", "flowers", "suppliers", "investors", "bills", "bill_days", "kharcha", "advances",
         "khaatas", "khaata_shares", "investor_entries"]
SKIP_COLUMNS = {"rev", "_key"}


def read_copy(body: bytes) -> dict[str, list[dict]]:
    """Tables from a device copy (.json) or a backup zip."""
    if body[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(body))
        except zipfile.BadZipFile:
            raise HTTPException(400, "This zip file is damaged.")
        tables = {}
        for name in z.namelist():
            parts = name.replace("\\", "/").split("/")
            if len(parts) >= 2 and parts[-2] == "_restore" and parts[-1].endswith(".json") and parts[-1] != "manifest.json":
                tables[parts[-1][:-5]] = json.loads(z.read(name))
        if not tables:
            raise HTTPException(400, "This zip has no _restore folder. Use a PetalBoard backup zip.")
        return tables
    try:
        data = json.loads(body)
    except ValueError:
        raise HTTPException(400, "This is not a PetalBoard copy (.json from a device, or a backup .zip).")
    if not isinstance(data, dict) or not isinstance(data.get("bills"), list) or not isinstance(data.get("suppliers"), list):
        raise HTTPException(400, "This file has no bills or suppliers. Use a PetalBoard copy.")
    return {k: v for k, v in data.items() if isinstance(v, list)}


def _columns(conn, table: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s",
        (table,)).fetchall()
    return {r["column_name"]: r["data_type"] for r in rows}


def _user_columns(conn, table: str) -> set[str]:
    """Columns of `table` that point at users (created_by, closed_by, ...)."""
    rows = conn.execute(
        """
        SELECT a.attname FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
        WHERE c.contype = 'f' AND c.conrelid = %s::regclass AND c.confrelid = 'users'::regclass
        """, (table,)).fetchall()
    return {r["attname"] for r in rows}


def restore_tables(conn, tables: dict[str, list[dict]]) -> dict[str, int]:
    have = conn.execute("SELECT (SELECT count(*) FROM suppliers) + (SELECT count(*) FROM bills) AS n").fetchone()["n"]
    if have:
        raise HTTPException(409, "This server already has data. A copy can only be loaded into a new, empty server, "
                                 "so nothing here is ever overwritten.")
    users = {r["id"] for r in conn.execute("SELECT id FROM users").fetchall()}
    counts = {}
    for t in ORDER:
        rows = tables.get(t) or []
        cols = _columns(conn, t)
        to_users = _user_columns(conn, t)
        if t == "shop_settings":
            rows = rows[:1]
            if rows:
                fields = {k: v for k, v in rows[0].items() if k in cols and k not in SKIP_COLUMNS | {"id"}}
                if fields:
                    conn.execute(f"UPDATE shop_settings SET {', '.join(f'{k} = %s' for k in fields)} WHERE id = 1",
                                 tuple(fields.values()))
            counts[t] = len(rows)
            continue
        for r in rows:
            fields = {}
            for k, v in r.items():
                if k not in cols or k in SKIP_COLUMNS:
                    continue
                if k in to_users and v not in users:
                    v = None
                if cols[k] == "jsonb" and v is not None:
                    v = Jsonb(v)
                fields[k] = v
            conn.execute(f"INSERT INTO {t} ({', '.join(fields)}) VALUES ({', '.join(['%s'] * len(fields))})",
                         tuple(fields.values()))
        if "id" in cols and rows:
            conn.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), (SELECT max(id) FROM {t}))")
        counts[t] = len(rows)
    return counts


@router.post("/restore")
async def restore(request: Request, user: dict = Depends(require("owner"))):
    body = await request.body()
    if not body:
        raise HTTPException(400, "No file was sent.")
    tables = read_copy(body)
    with db.transaction(user["id"]) as conn:
        counts = restore_tables(conn, tables)
    return {"restored": counts}
