from __future__ import annotations

import json
from typing import Any, cast

import redis
from redis.exceptions import ResponseError

from prism_infra.bus.base import StreamMessage


class RedisStreamsBus:
    def __init__(self, redis_url: str) -> None:
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def publish(self, stream: str, event: dict[str, Any]) -> str:
        return str(self.client.xadd(stream, {"event": json.dumps(event, separators=(",", ":"))}))

    def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 100,
        block_ms: int = 5000,
    ) -> list[StreamMessage]:
        self._ensure_group(stream, group)
        response = cast(
            list[tuple[str, list[tuple[str, dict[str, str]]]]],
            self.client.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">"},
                count=count,
                block=block_ms,
            ),
        )
        messages: list[StreamMessage] = []
        for _, entries in response:
            for message_id, fields in entries:
                messages.append(StreamMessage(id=message_id, event=json.loads(fields["event"])))
        return messages

    def ack(self, stream: str, group: str, message_ids: list[str]) -> None:
        if message_ids:
            self.client.xack(stream, group, *message_ids)

    def _ensure_group(self, stream: str, group: str) -> None:
        try:
            self.client.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
