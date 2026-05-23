import logging
import os
import time
from datetime import UTC, datetime, timedelta

import psycopg

from prism_infra.storage import PostgresLogStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("partition-cron")

INTERVAL_S = 86_400  # 24 hours
RETENTION_DAYS = int(os.getenv("PARTITION_RETENTION_DAYS", "30"))
DATABASE_URL = os.environ["DATABASE_URL"]

REQUIRED_PARTITION_TABLES = ("inference_logs", "tool_invocations")


def run_once() -> None:
    PostgresLogStore(DATABASE_URL).ensure_partitions(retention_days=RETENTION_DAYS)
    log.info("partition-cron ensured partitions (retention=%d days)", RETENTION_DAYS)
    _assert_partitions_present()


def _assert_partitions_present() -> None:
    # Without this, the `*_default` partition silently swallows rows when the
    # ensure step is misconfigured — making the bug invisible until query time.
    today = datetime.now(UTC).date()
    expected_dates = (today, today + timedelta(days=1))
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        for parent in REQUIRED_PARTITION_TABLES:
            cur.execute(
                """
                SELECT inhrelid::regclass::text
                FROM pg_inherits
                WHERE inhparent = %s::regclass
                """,
                (parent,),
            )
            present = {row[0] for row in cur.fetchall()}
            for d in expected_dates:
                expected = f"{parent}_{d.strftime('%Y%m%d')}"
                if expected not in present:
                    log.error(
                        "partition-cron: missing expected partition %s; rows will "
                        "fall into %s_default and be invisible to date-range queries",
                        expected,
                        parent,
                    )


def main() -> None:
    log.info("partition-cron started")
    while True:
        run_once()
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
