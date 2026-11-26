"""Start the isolated Render test service, never the public Vercel demo."""
import hashlib
import json
import os
import subprocess
import sys

import certifi
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def main():
    if os.getenv("RM_HOSTED") != "1" or os.getenv("RM_MODE") != "operational" or os.getenv("VERCEL"):
        raise SystemExit("Invalid Render test environment.")
    token = os.getenv("RM_HOMOLOGATION_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("Missing test credential.")
    try:
        url = make_url(os.environ["DATABASE_URL"])
        if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
            raise ValueError
        if not url.host or not url.host.endswith(".neon.tech") or url.database != "rm_control":
            raise ValueError
        if "-pooler" in url.host:
            raise ValueError
        url = url.set(drivername="postgresql+psycopg").update_query_dict({"sslmode": "verify-full", "sslrootcert": certifi.where()})
        os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
        port = int(os.environ.get("PORT", "10000"))
        if not 1 <= port <= 65535:
            raise ValueError
    except (ArgumentError, KeyError, ValueError, TypeError):
        raise SystemExit("Use the direct TLS connection for the Neon rm_control test database.") from None
    os.environ["RM_IDENTITIES_JSON"] = json.dumps({
        hashlib.sha256(token.encode()).hexdigest(): {
            "subject": "test-admin", "organization": "rm-homologacao",
            "role": "admin", "stores": ["2"]
        }
    })
    os.environ["RM_CALENDARS_JSON"] = "{}"
    from app.config import load_settings
    if not load_settings().enabled:
        raise SystemExit("Invalid test environment settings.")
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], capture_output=True)
    if result.returncode:
        raise SystemExit("Database migration failed; inspect configuration privately.")
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(port), "--no-access-log", "--workers", "1"])


if __name__ == "__main__":
    main()
