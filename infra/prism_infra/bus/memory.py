from __future__ import annotations

from typing import Any

from prism_infra.bus.base import StreamMessage


class InMemoryBus:
    def __init__(self) -> None:
        self.streams: dict[str, list[StreamMessage]] = {}
        self.offsets: dict[tuple[str, str], int] = {}

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
        return batch

    def ack(self, stream: str, group: str, message_ids: list[str]) -> None:
        return None
