"""Test setup: a fresh database for each test run.

Needs a PostgreSQL the tests can create databases in, given by TEST_DATABASE_ADMIN_URL
(default: the throwaway trust-auth cluster on port 55432, see CLAUDE.md).
"""

import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://postgres@127.0.0.1:55432/postgres")
NAME = "petalboard_test_" + uuid.uuid4().hex[:8]
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + "/" + NAME

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DATABASE_URL"] = TEST_URL


@pytest.fixture(scope="session", autouse=True)
def database():
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {NAME}")
    import db
    db.migrate(TEST_URL)
    yield TEST_URL
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE {NAME} WITH (FORCE)")


@pytest.fixture(scope="session")
def client(database):
    from fastapi.testclient import TestClient

    import db
    from main import app
    from security import hash_password

    with db.transaction() as conn:
        conn.execute("INSERT INTO users (username, display_name, role, password_hash) "
                     "VALUES ('owner', 'Owner', 'owner', %s), ('clerk', 'Clerk', 'clerk', %s), "
                     "('viewer', 'Viewer', 'viewer', %s)",
                     (hash_password("owner-pass-1"), hash_password("clerk-pass-1"), hash_password("viewer-pass-1")))
    with TestClient(app) as c:
        token = c.post("/auth/login", json={"username": "owner", "password": "owner-pass-1"}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c
