"""partition-cron — Phase 0 stub.

Runs nightly. Creates tomorrow's inference_logs_YYYYMMDD partition and drops
partitions older than PARTITION_RETENTION_DAYS. Full implementation in
Phase 1.
"""
import logging
import os
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("partition-cron")

INTERVAL_S = 86_400  # 24 hours
RETENTION_DAYS = int(os.getenv("PARTITION_RETENTION_DAYS", "30"))


def run_once() -> None:
    log.info("partition-cron run (Phase 0 stub — no-op, retention=%d days)", RETENTION_DAYS)


def main() -> None:
    log.info("partition-cron started")
    while True:
        run_once()
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
