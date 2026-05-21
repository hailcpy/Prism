"""metrics-reconciler cron — Phase 0 stub.

Runs every 5 minutes. Recomputes the last N closed minute-buckets directly
from inference_logs and UPSERT-replaces into metrics_minute. Full
implementation in Phase 6.
"""

import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("metrics-reconciler")

INTERVAL_S = 300  # 5 minutes


def run_once() -> None:
    log.info("reconciler run (Phase 0 stub — no-op)")


def main() -> None:
    log.info("metrics-reconciler started")
    while True:
        run_once()
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
