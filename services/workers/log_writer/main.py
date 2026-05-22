from __future__ import annotations

import logging
import os
import pathlib
import time
from datetime import UTC, datetime

from prism_infra.bus import Bus, RedisStreamsBus, StreamMessage
from prism_infra.events import event_from_wire, tool_event_from_wire
from prism_infra.models import InferenceEvent, ToolInvocationEvent
from prism_infra.storage import LogStore, PostgresLogStore

logging.basicConfig(
    level=os.getenv("PRISM_LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s"
)
log = logging.getLogger("log-writer")

STREAM = "inference.logged"
DEAD_STREAM = "inference.dead"
GROUP = "cg-writer"
CONSUMER = os.getenv("PRISM_CONSUMER_NAME", "log-writer-1")
MAX_BATCH_SIZE = 1000
FLUSH_INTERVAL_SECONDS = 5.0
PENDING_IDLE_MS = int(os.getenv("PRISM_PENDING_IDLE_MS", "30000"))
HEALTH_FILE = pathlib.Path("/tmp/prism-log-writer-healthy")


def process_messages(
    messages: list[StreamMessage],
    *,
    bus: Bus,
    store: LogStore,
    stream: str = STREAM,
    group: str = GROUP,
) -> tuple[int, int]:
    events: list[InferenceEvent] = []
    tool_events: list[ToolInvocationEvent] = []
    valid_message_ids: list[str] = []
    rejected_message_ids: list[str] = []

    for message in messages:
        try:
            if message.event.get("event_type") == "tool_invocation":
                tool_events.append(tool_event_from_wire(message.event))
            else:
                events.append(event_from_wire(message.event))
            valid_message_ids.append(message.id)
        except (KeyError, TypeError, ValueError) as exc:
            bus.publish(
                DEAD_STREAM,
                {
                    **message.event,
                    "dead_reason": f"invalid event: {exc}",
                    "failed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                },
            )
            rejected_message_ids.append(message.id)

    if events:
        store.write_logs_batch(events)

    if tool_events:
        store.write_tool_events_batch(tool_events)

    if events or tool_events:
        bus.ack(stream, group, valid_message_ids)

    if rejected_message_ids:
        bus.ack(stream, group, rejected_message_ids)

    return len(events) + len(tool_events), len(rejected_message_ids)


def run(
    *,
    bus: Bus,
    store: LogStore,
    stream: str = STREAM,
    group: str = GROUP,
    consumer: str = CONSUMER,
) -> None:
    HEALTH_FILE.touch()
    log.info("log-writer started")
    pending: list[StreamMessage] = []
    last_flush = time.monotonic()

    while True:
        HEALTH_FILE.touch()
        messages = bus.claim_pending(
            stream,
            group,
            consumer,
            min_idle_ms=PENDING_IDLE_MS,
            count=MAX_BATCH_SIZE - len(pending),
        )
        if not messages:
            messages = bus.consume(
                stream,
                group,
                consumer,
                count=MAX_BATCH_SIZE - len(pending),
                block_ms=1000,
            )
        else:
            log.info("claimed pending messages count=%s", len(messages))

        pending.extend(messages)

        should_flush = pending and (
            len(pending) >= MAX_BATCH_SIZE
            or time.monotonic() - last_flush >= FLUSH_INTERVAL_SECONDS
        )
        if not should_flush:
            continue

        accepted, rejected = process_messages(
            pending, bus=bus, store=store, stream=stream, group=group
        )
        log.info("flushed accepted=%s rejected=%s", accepted, rejected)
        pending = []
        last_flush = time.monotonic()


def main() -> None:
    run(bus=_bus_from_env(), store=_store_from_env())


def _bus_from_env() -> Bus:
    return RedisStreamsBus(os.environ["REDIS_URL"])


def _store_from_env() -> LogStore:
    return PostgresLogStore(os.environ["DATABASE_URL"])


if __name__ == "__main__":
    main()
