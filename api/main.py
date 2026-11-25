"""PetalBoard API (FastAPI). Runs on the Oracle server; the web app on Cloudflare calls it."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    yield


app = FastAPI(title="PetalBoard API", lifespan=lifespan)

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
