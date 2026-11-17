"""Passwords, login sessions and role checks."""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException

import config
import db

_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)

ROLE_RANK = {"viewer": 0, "clerk": 1, "manager": 2, "owner": 3}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt_b64, digest_b64 = stored.split("$")
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=config.SESSION_DAYS)
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
        (_token_hash(token), user_id, expires),
    )
    return token


def end_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_token_hash(token),))


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not logged in")
    return authorization.split(" ", 1)[1].strip()


def current_user(authorization: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    with db.transaction() as conn:
        user = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.role, s.token_hash
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = %s AND s.expires_at > now() AND u.active
            """,
            (_token_hash(token),),
        ).fetchone()
        if not user:
            raise HTTPException(401, "Session expired, log in again")
        # Sliding expiry: every use pushes the end date forward, so a device that is used stays logged in.
        conn.execute(
            "UPDATE sessions SET last_seen = now(), expires_at = now() + make_interval(days => %s) WHERE token_hash = %s",
            (config.SESSION_DAYS, user["token_hash"]),
        )
    user.pop("token_hash")
    user["token"] = token
    return user


def require(role: str):
    """Dependency: the logged-in user must have at least this role."""

    def check(user: dict = Depends(current_user)) -> dict:
        if ROLE_RANK[user["role"]] < ROLE_RANK[role]:
            raise HTTPException(403, "You do not have permission for this")
        return user

    return check
