from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _require_url(env_key: str, expected_db: str) -> str:
    url = os.getenv(env_key, "").strip()
    if not url:
        raise SystemExit(f"{env_key} is missing from .env")
    parsed = urlparse(url)
    dbname = (parsed.path or "").lstrip("/")
    if dbname != expected_db:
        raise SystemExit(
            f"{env_key} must target database {expected_db!r}, got {dbname!r}"
        )
    return url


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    runtime_url = _require_url("DATABASE_URL", "odoo_agent")

    try:
        with psycopg.connect(runtime_url) as conn:
            current = conn.execute("SELECT current_database()").fetchone()
            assert current is not None
            if current[0] != "odoo_agent":
                raise SystemExit(
                    f"Refusing to continue: connected to {current[0]!r}, "
                    "expected 'odoo_agent'"
                )
    except psycopg.OperationalError as exc:
        print(f"ERROR: could not connect: {str(exc).splitlines()[0]}")
        raise SystemExit(1) from None

    from pipeline.run_store import RunStore

    try:
        RunStore(runtime_url).ensure_schema()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: ensure_schema failed: {exc}")
        raise SystemExit(1) from None

    print("tables in odoo_agent: ensured")
    print("init_db complete")


if __name__ == "__main__":
    sys.path.insert(0, str(PROJECT_ROOT))
    main()
