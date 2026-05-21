import logging
import os
import time

from prism_infra.storage import PostgresLogStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("partition-cron")

INTERVAL_S = 86_400  # 24 hours
RETENTION_DAYS = int(os.getenv("PARTITION_RETENTION_DAYS", "30"))
DATABASE_URL = os.environ["DATABASE_URL"]


def run_once() -> None:
    PostgresLogStore(DATABASE_URL).ensure_partitions(retention_days=RETENTION_DAYS)
    log.info("partition-cron ensured partitions (retention=%d days)", RETENTION_DAYS)


def main() -> None:
    log.info("partition-cron started")
    while True:
        run_once()
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
