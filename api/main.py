"""PetalBoard API (FastAPI). Runs on the Oracle server; the web app on Cloudflare calls it."""

import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

import config
import db
import routes_auth
import routes_bills
import routes_export
import routes_investors
import routes_reports
import routes_suppliers
import routes_restore
import routes_sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.migrate()
    with db.transaction() as conn:  # ids of saves are kept long enough for any phone to come back online
        conn.execute("DELETE FROM applied_changes WHERE at < now() - interval '180 days'")
    yield


app = FastAPI(title="PetalBoard API", lifespan=lifespan)


def _applied(change_id: str):
    with db.transaction() as conn:
        return conn.execute("SELECT status, response FROM applied_changes WHERE id = %s", (change_id,)).fetchone()


def _remember(change_id: str, status: int, body: bytes):
    try:
        answer = json.loads(body) if body else None
    except ValueError:
        answer = None
    with db.transaction() as conn:
        conn.execute("UPDATE applied_changes SET status = %s, response = %s WHERE id = %s",
                     (status, json.dumps(answer), change_id))


@app.middleware("http")
async def apply_once(request: Request, call_next):
    """A save with X-Change-Id is applied once. Sent again (its answer was lost on the way), it is not
    applied again: the first answer comes back, marked X-Change-Replayed."""
    change_id = request.headers.get("x-change-id")
    if request.method not in ("POST", "PUT", "PATCH", "DELETE") or not change_id or len(change_id) > 80:
        return await call_next(request)
    done = await run_in_threadpool(_applied, change_id)
    if done:
        return JSONResponse(done["response"], status_code=done["status"] or 200, headers={"X-Change-Replayed": "1"})
    change = {"id": change_id, "method": request.method, "path": request.url.path, "recorded": False}
    token = db.CHANGE.set(change)
    try:
        response = await call_next(request)
    finally:
        db.CHANGE.reset(token)
    if not change["recorded"] or response.status_code >= 400:
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    await run_in_threadpool(_remember, change_id, response.status_code, body)
    headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    return Response(body, status_code=response.status_code, headers=headers, media_type=response.media_type)


# Added last = outermost, so every answer (a replayed one too) gets the CORS headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=False,          # the app sends a bearer token, not cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (routes_auth, routes_suppliers, routes_bills, routes_reports, routes_investors, routes_export,
               routes_sync, routes_restore):
    app.include_router(module.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), reload=True)
