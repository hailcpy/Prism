from __future__ import annotations

from typing import Any

from prism_infra.bus.base import StreamMessage


class InMemoryBus:
    def __init__(self) -> None:
        self.streams: dict[str, list[StreamMessage]] = {}
        self.offsets: dict[tuple[str, str], int] = {}
        self.pending: dict[tuple[str, str], list[StreamMessage]] = {}

    def publish(self, stream: str, event: dict[str, Any]) -> str:
        messages = self.streams.setdefault(stream, [])
        message_id = f"{len(messages) + 1}-0"
        messages.append(StreamMessage(id=message_id, event=event))
        return message_id

    def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 100,
        block_ms: int = 5000,
    ) -> list[StreamMessage]:
        key = (stream, group)
        offset = self.offsets.get(key, 0)
        batch = self.streams.get(stream, [])[offset : offset + count]
        self.offsets[key] = offset + len(batch)
        self.pending.setdefault(key, []).extend(batch)
        return batch

    def claim_pending(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int,
        count: int = 100,
    ) -> list[StreamMessage]:
        if min_idle_ms > 0:
            return []
        return self.pending.get((stream, group), [])[:count]

    def ack(self, stream: str, group: str, message_ids: list[str]) -> None:
        if not message_ids:
            return
        key = (stream, group)
        acked = set(message_ids)
        self.pending[key] = [
            message for message in self.pending.get(key, []) if message.id not in acked
        ]
