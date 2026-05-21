"""log-writer worker — Phase 0 stub.

Consumes inference.logged Redis stream (cg-writer) and bulk-inserts into
inference_logs. Full implementation in Phase 2.
"""
import logging
import time
import pathlib

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("log-writer")

HEALTH_FILE = pathlib.Path("/tmp/olive-log-writer-healthy")


def main() -> None:
    HEALTH_FILE.touch()
    log.info("log-writer started (Phase 0 stub)")
    while True:
        HEALTH_FILE.touch()
        log.info("heartbeat")
        time.sleep(30)


if __name__ == "__main__":
    main()
