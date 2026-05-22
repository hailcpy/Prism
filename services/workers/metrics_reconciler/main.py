"""metrics-reconciler — canonical rollup pass over inference_logs (ADR-0010 Track 2)."""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime, timedelta

from prism_infra.storage import LogStore, PostgresLogStore

logging.basicConfig(
    level=os.getenv("PRISM_LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s"
)
log = logging.getLogger("metrics-reconciler")

INTERVAL_S = int(os.getenv("PRISM_RECONCILER_INTERVAL_S", "300"))
WINDOW_MINUTES = int(os.getenv("PRISM_RECONCILER_WINDOW_MINUTES", "15"))


def run_once(store: LogStore, *, window_minutes: int = WINDOW_MINUTES) -> int:
    now = datetime.now(UTC)
    end = now.replace(second=0, microsecond=0)
    start = end - timedelta(minutes=window_minutes)
    rows = store.reconcile_metrics(start, end)
    log.info("reconciled rows=%s start=%s end=%s", rows, start.isoformat(), end.isoformat())
    return rows


def main() -> None:
    store = PostgresLogStore(os.environ["DATABASE_URL"])
    log.info("metrics-reconciler started interval_s=%s window_m=%s", INTERVAL_S, WINDOW_MINUTES)
    while True:
        try:
            run_once(store)
        except Exception:  # noqa: BLE001 — keep the cron alive
            log.exception("reconciler run failed")
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
