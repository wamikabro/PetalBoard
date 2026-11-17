"""Settings from environment variables. Never put secrets in the repo."""

import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:55432/petalboard_dev")

# Browser origins allowed to call the API (the app on Cloudflare, and local dev).
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:4321").split(",")
    if o.strip()
]

# A login stays valid on its device until it goes unused for this many days (each use renews it).
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "365"))
