from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class StreamMessage:
    id: str
    event: dict[str, Any]


class Bus(Protocol):
    def publish(self, stream: str, event: dict[str, Any]) -> str: ...

    def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 100,
        block_ms: int = 5000,
    ) -> list[StreamMessage]: ...

    def ack(self, stream: str, group: str, message_ids: list[str]) -> None: ...
