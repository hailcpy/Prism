from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient

from chatbot_api import main as chatbot_main
from chatbot_api.main import Conversation, Message, app


class FakeChatStore:
    def __init__(self) -> None:
        self.conversations: dict[str, Conversation] = {}
        self.messages: list[Message] = []

    def create_conversation(self, model_default: str, system_prompt: str | None) -> Conversation:
        now = datetime.now(UTC)
        conversation = Conversation(
            id=str(uuid.uuid4()),
            model_default=model_default,
            system_prompt=system_prompt,
            created_at=now,
            updated_at=now,
        )
        self.conversations[conversation.id] = conversation
        return conversation

    def list_conversations(self, limit: int = 50) -> list[Conversation]:
        conversations = []
        for conversation in self.conversations.values():
            count = sum(
                1 for message in self.messages if message.conversation_id == conversation.id
            )
            conversations.append(replace(conversation, message_count=count))
        return conversations[:limit]

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        return self.conversations.get(conversation_id)

    def list_messages(self, conversation_id: str) -> list[Message]:
        return [message for message in self.messages if message.conversation_id == conversation_id]

    def create_message(
        self,
        conversation_id: str,
        role: Literal["user", "assistant", "system"],
        content: str,
    ) -> Message:
        message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role=role,
            content=content,
            created_at=datetime.now(UTC),
        )
        self.messages.append(message)
        return message

    def update_message_content(self, message_id: str, content: str) -> Message:
        for index, message in enumerate(self.messages):
            if message.id == message_id:
                updated = replace(message, content=content)
                self.messages[index] = updated
                return updated
        raise KeyError(message_id)

    def delete_message(self, message_id: str) -> None:
        self.messages = [m for m in self.messages if m.id != message_id]


class FakePrismClient:
    def install(self) -> None:
        return None


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data_parts: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_parts.append(line[5:].strip())
        events.append((event, json.loads("\n".join(data_parts))))
    return events


@pytest.fixture
def chatbot_client(monkeypatch):
    store = FakeChatStore()
    app.state.chat_store = store
    app.state.prism_client = FakePrismClient()

    chunks = [
        {"choices": [{"delta": {"content": "he"}}]},
        {"choices": [{"delta": {"content": "llo"}}]},
    ]
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs):
        captured["kwargs"] = kwargs

        async def iterator():
            for chunk in chunks:
                yield chunk

        return iterator()

    monkeypatch.setattr(chatbot_main.litellm, "acompletion", fake_acompletion)
    return TestClient(app), store, captured


def test_chatbot_streams_assistant_tokens_via_sse(chatbot_client) -> None:
    client, store, captured = chatbot_client

    created = client.post("/v1/conversations", json={"model_default": "gpt-4o"})
    conversation_id = created.json()["conversation_id"]
    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "hi", "model": "gpt-4o"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    event_names = [event for event, _ in events]
    assert event_names[0] == "user_message"
    assert event_names[1] == "assistant_message"
    deltas = [data["delta"] for event, data in events if event == "token"]
    assert "".join(deltas) == "hello"
    done = next(data for event, data in events if event == "done")
    assert done["inference_id"]
    prism_meta = captured["kwargs"]["metadata"]["prism"]
    assert prism_meta["conversation_id"] == conversation_id
    assert prism_meta["inference_id"] == done["inference_id"]

    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "hello"


def test_chatbot_does_not_persist_assistant_row_on_stream_failure(monkeypatch) -> None:
    store = FakeChatStore()
    app.state.chat_store = store
    app.state.prism_client = FakePrismClient()

    async def fake_acompletion(**kwargs):
        async def iterator():
            yield {"choices": [{"delta": {"content": "partial"}}]}
            raise RuntimeError("provider blew up")

        return iterator()

    monkeypatch.setattr(chatbot_main.litellm, "acompletion", fake_acompletion)
    client = TestClient(app)

    created = client.post("/v1/conversations", json={"model_default": "gpt-4o"})
    conversation_id = created.json()["conversation_id"]
    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "hi", "model": "gpt-4o"},
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    event_names = [event for event, _ in events]
    assert "error" in event_names
    assert "done" not in event_names

    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["messages"]
    assert [message["role"] for message in messages] == ["user"]


def test_chatbot_does_not_persist_assistant_row_on_empty_response(monkeypatch) -> None:
    store = FakeChatStore()
    app.state.chat_store = store
    app.state.prism_client = FakePrismClient()

    async def fake_acompletion(**kwargs):
        async def iterator():
            if False:
                yield {}
            return

        return iterator()

    monkeypatch.setattr(chatbot_main.litellm, "acompletion", fake_acompletion)
    client = TestClient(app)

    created = client.post("/v1/conversations", json={"model_default": "gpt-4o"})
    conversation_id = created.json()["conversation_id"]
    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "hi", "model": "gpt-4o"},
    )

    events = _parse_sse(response.text)
    assert any(event == "error" for event, _ in events)
    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["messages"]
    assert [message["role"] for message in messages] == ["user"]
