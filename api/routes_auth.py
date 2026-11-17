"""Login, logout and user accounts."""

import re
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import db
from security import create_session, current_user, end_session, hash_password, require, verify_password

router = APIRouter(tags=["auth"])

ROLE_PATTERN = "^(owner|manager|clerk|viewer)$"


class Login(BaseModel):
    password: str = Field(min_length=1)
    username: str | None = None   # optional: people log in with their password alone


# Wrong-password limit, kept in memory (the API runs as one process): per address and overall.
_FAILS: dict[str, deque] = defaultdict(deque)
WINDOW = 15 * 60
PER_ADDRESS = 10
OVERALL = 40


def _too_many(addr: str) -> bool:
    now = time.monotonic()
    for key in (addr, "*"):
        q = _FAILS[key]
        while q and now - q[0] > WINDOW:
            q.popleft()
    return len(_FAILS[addr]) >= PER_ADDRESS or len(_FAILS["*"]) >= OVERALL


def _failed(addr: str) -> None:
    now = time.monotonic()
    _FAILS[addr].append(now)
    _FAILS["*"].append(now)


def find_user_by_password(conn, password: str, skip_id: int | None = None):
    """The active user with this password (passwords are unique, so there is at most one)."""
    for u in conn.execute(
            "SELECT id, username, display_name, role, password_hash FROM users WHERE active AND id <> coalesce(%s, -1)",
            (skip_id,)).fetchall():
        if verify_password(password, u["password_hash"]):
            return u
    return None


def check_unique_password(conn, password: str, user_id: int | None = None) -> None:
    if find_user_by_password(conn, password, skip_id=user_id):
        raise HTTPException(400, "Someone else already uses that password. Choose a different one.")


@router.post("/auth/login")
def login(body: Login, request: Request):
    addr = request.client.host if request.client else "?"
    if _too_many(addr):
        raise HTTPException(429, "Too many wrong passwords. Wait 15 minutes and try again.")
    with db.transaction() as conn:
        if body.username:
            user = conn.execute(
                "SELECT id, username, display_name, role, password_hash FROM users WHERE lower(username) = lower(%s) AND active",
                (body.username,),
            ).fetchone()
            if user and not verify_password(body.password, user["password_hash"]):
                user = None
        else:
            user = find_user_by_password(conn, body.password)
        if not user:
            _failed(addr)
            raise HTTPException(401, "Wrong password")
        token = create_session(conn, user["id"])
    return {"token": token, "user": {k: user[k] for k in ("id", "username", "display_name", "role")}}


@router.post("/auth/logout")
def logout(user: dict = Depends(current_user)):
    with db.transaction() as conn:
        end_session(conn, user["token"])
    return {"ok": True}


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return {k: user[k] for k in ("id", "username", "display_name", "role")}


class NewUser(BaseModel):
    username: str | None = Field(default=None, min_length=2)   # made from the name when left out
    display_name: str
    role: str = Field(pattern=ROLE_PATTERN)
    password: str = Field(min_length=8)


class UserChange(BaseModel):
    display_name: str | None = None
    role: str | None = Field(default=None, pattern=ROLE_PATTERN)
    active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


@router.get("/users")
def list_users(user: dict = Depends(require("owner"))):
    with db.transaction(user["id"]) as conn:
        return conn.execute("SELECT id, username, display_name, role, active, created_at FROM users ORDER BY id").fetchall()


@router.post("/users")
def create_user(body: NewUser, user: dict = Depends(require("owner"))):
    with db.transaction(user["id"]) as conn:
        base = body.username or re.sub(r"[^a-z0-9]+", "", body.display_name.lower()) or "user"
        username, n = base, 1
        while conn.execute("SELECT 1 FROM users WHERE lower(username) = lower(%s)", (username,)).fetchone():
            if body.username:
                raise HTTPException(400, "That username is taken")
            n += 1
            username = f"{base}{n}"
        body.username = username
        check_unique_password(conn, body.password)
        return conn.execute(
            "INSERT INTO users (username, display_name, role, password_hash) VALUES (%s, %s, %s, %s) "
            "RETURNING id, username, display_name, role, active",
            (body.username, body.display_name, body.role, hash_password(body.password)),
        ).fetchone()


@router.patch("/users/{user_id}")
def change_user(user_id: int, body: UserChange, user: dict = Depends(require("owner"))):
    with db.transaction(user["id"]) as conn:
        fields = body.model_dump(exclude_none=True)
        if "password" in fields:
            check_unique_password(conn, fields["password"], user_id)
            fields["password_hash"] = hash_password(fields.pop("password"))
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
        if fields.get("active") is False:
            if user_id == user["id"]:
                raise HTTPException(400, "You cannot switch off your own account")
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
        if not fields:
            raise HTTPException(400, "Nothing to change")
        sets = ", ".join(f"{k} = %s" for k in fields)
        row = conn.execute(
            f"UPDATE users SET {sets} WHERE id = %s RETURNING id, username, display_name, role, active",
            (*fields.values(), user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        return row
