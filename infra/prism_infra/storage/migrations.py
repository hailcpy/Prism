from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import psycopg

logger = logging.getLogger(__name__)

_MIGRATIONS_LOCK_KEY = 0x70_72_69_73_6D_6D_69_67  # ascii: "prismig"

_MIGRATIONS_SQL_PATH = Path(__file__).resolve().parents[3] / "infra" / "sql" / "migrations.sql"


def run_migrations(database_url: str, sql_path: Path | None = None) -> None:
    """Apply idempotent catch-up migrations.

    Safe to call on every service boot. Uses a session-level advisory lock so
    concurrent boots serialize instead of racing on DDL.
    """
    path = sql_path or _MIGRATIONS_SQL_PATH
    if not path.exists():
        logger.warning("migrations: %s not found, skipping", path)
        return
    sql_text = path.read_text()
    if not sql_text.strip():
        return
    with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (_MIGRATIONS_LOCK_KEY,))
        try:
            cur.execute(cast(Any, sql_text))
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATIONS_LOCK_KEY,))
    logger.info("migrations: applied %s", path)
