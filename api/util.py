"""Small helpers shared by the routes."""

from fastapi import HTTPException


def update_row(conn, table: str, row_id: int, fields: dict, allowed: set[str]):
    """UPDATE only the given, allowed columns. Returns the updated row."""
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        raise HTTPException(400, "Nothing to change")
    sets = ", ".join(f"{k} = %s" for k in fields)
    row = conn.execute(f"UPDATE {table} SET {sets} WHERE id = %s RETURNING *", (*fields.values(), row_id)).fetchone()
    if not row:
        raise HTTPException(404, f"Not found in {table}")
    return row


def insert_row(conn, table: str, fields: dict):
    cols = ", ".join(fields)
    marks = ", ".join(["%s"] * len(fields))
    return conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks}) RETURNING *", tuple(fields.values())).fetchone()


def get_or_404(conn, table: str, row_id: int):
    row = conn.execute(f"SELECT * FROM {table} WHERE id = %s", (row_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Not found in {table}")
    return row
