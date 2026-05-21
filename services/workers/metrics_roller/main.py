"""metrics-roller worker — Phase 0 stub.

Consumes inference.logged Redis stream (cg-roller), maintains 60-second
tumbling windows, and upserts rows into metrics_minute. Full implementation
in Phase 6.
"""
import logging
import time
import pathlib

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("metrics-roller")

HEALTH_FILE = pathlib.Path("/tmp/olive-metrics-roller-healthy")


def main() -> None:
    HEALTH_FILE.touch()
    log.info("metrics-roller started (Phase 0 stub)")
    while True:
        HEALTH_FILE.touch()
        log.info("heartbeat")
        time.sleep(30)


if __name__ == "__main__":
    main()
