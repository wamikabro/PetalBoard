"""Login, logout and user accounts."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import db
from security import create_session, current_user, end_session, hash_password, require, verify_password

router = APIRouter(tags=["auth"])

ROLE_PATTERN = "^(owner|manager|clerk|viewer)$"


class Login(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: Login):
    with db.transaction() as conn:
        user = conn.execute(
            "SELECT id, username, display_name, role, password_hash FROM users WHERE lower(username) = lower(%s) AND active",
            (body.username,),
        ).fetchone()
        if not user or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "Wrong username or password")
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
    username: str = Field(min_length=2)
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
        if conn.execute("SELECT 1 FROM users WHERE lower(username) = lower(%s)", (body.username,)).fetchone():
            raise HTTPException(400, "That username is taken")
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
