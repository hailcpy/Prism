from __future__ import annotations

import asyncio
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
        status: str = "ok",
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role=role,
            status=status,
            content=content,
            created_at=datetime.now(UTC),
            metadata=metadata or {},
        )
        self.messages.append(message)
        return message

    def update_message_content(
        self,
        message_id: str,
        content: str,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        for index, message in enumerate(self.messages):
            if message.id == message_id:
                updated = replace(
                    message,
                    content=content,
                    status=status if status is not None else message.status,
                    metadata=metadata if metadata is not None else message.metadata,
                )
                self.messages[index] = updated
                return updated
        raise KeyError(message_id)

    def delete_message(self, message_id: str) -> None:
        self.messages = [m for m in self.messages if m.id != message_id]

    def update_conversation_title(self, conversation_id: str, title: str) -> None:
        conv = self.conversations.get(conversation_id)
        if conv is not None:
            self.conversations[conversation_id] = replace(conv, title=title)

    def delete_conversation(self, conversation_id: str) -> None:
        self.conversations.pop(conversation_id, None)
        self.messages = [m for m in self.messages if m.conversation_id != conversation_id]


class FakePrismClient:
    def install(self) -> None:
        return None


class FakeAgent:
    def __init__(self, chunks: list[dict[str, Any]], exc: BaseException | None = None) -> None:
        self.chunks = chunks
        self.exc = exc

    async def stream_async(self, prompt: str, *, invocation_state: dict[str, Any]):
        for chunk in self.chunks:
            yield chunk
        if self.exc is not None:
            raise self.exc


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

    class FakeCredentialStore:
        def get_default_credential_for_provider(self, provider: str):
            return type(
                "Cred",
                (),
                {
                    "provider": provider,
                    "secrets": {"api_key": "test"},
                    "metadata": {},
                },
            )()

        def list_credentials(self):
            return []

        def get_credential_with_secrets(self, credential_id: str):
            return None

    app.state.credential_store = FakeCredentialStore()

    chunks = [
        {"data": "he"},
        {"data": "llo"},
    ]
    captured: dict[str, Any] = {}

    def fake_build_agent(**kwargs):
        captured["kwargs"] = kwargs
        return FakeAgent(chunks)

    monkeypatch.setattr(chatbot_main, "_build_agent", fake_build_agent)
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
    prism_meta = captured["kwargs"]["prism_metadata"]["prism"]
    assert prism_meta["conversation_id"] == conversation_id
    assert prism_meta["inference_id"] == done["inference_id"]
    assert prism_meta["provider"] == "openai"

    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "hello"
    assert messages[1]["status"] == "ok"


def test_chatbot_streams_and_persists_thinking_traces(monkeypatch) -> None:
    store = FakeChatStore()
    app.state.chat_store = store
    app.state.prism_client = FakePrismClient()

    def fake_build_agent(**kwargs):
        return FakeAgent(
            [
                {"thinking_delta": "plan "},
                {
                    "event": {
                        "type": "modelContentBlockDeltaEvent",
                        "delta": {"type": "thinkingDelta", "text": "then answer"},
                    }
                },
                {"data": "done"},
            ]
        )

    monkeypatch.setattr(chatbot_main, "_build_agent", fake_build_agent)
    client = TestClient(app)

    created = client.post("/v1/conversations", json={"model_default": "gpt-4o"})
    conversation_id = created.json()["conversation_id"]
    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "hi", "model": "gpt-4o"},
    )

    events = _parse_sse(response.text)
    thinking = [data["delta"] for event, data in events if event == "thinking"]
    assert "".join(thinking) == "plan then answer"

    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["messages"]
    assert messages[1]["thinking_trace"] == "plan then answer"


def test_chatbot_routes_bedrock_application_profiles_through_converse(chatbot_client) -> None:
    client, _, captured = chatbot_client
    profile_arn = (
        "arn:aws:bedrock:us-west-2:823998119176:application-inference-profile/hnxtndg2c380"
    )

    created = client.post("/v1/conversations", json={"model_default": f"bedrock/{profile_arn}"})
    conversation_id = created.json()["conversation_id"]
    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "hi"},
    )

    assert response.status_code == 200
    assert captured["kwargs"]["model"] == f"bedrock/converse/{profile_arn}"
    assert captured["kwargs"]["prism_metadata"]["prism"]["provider"] == "bedrock"


def test_chatbot_leaves_explicit_bedrock_converse_models_unchanged(chatbot_client) -> None:
    client, _, captured = chatbot_client
    model = (
        "bedrock/converse/"
        "arn:aws:bedrock:us-west-2:823998119176:application-inference-profile/hnxtndg2c380"
    )

    created = client.post("/v1/conversations", json={"model_default": model})
    conversation_id = created.json()["conversation_id"]
    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "hi"},
    )

    assert response.status_code == 200
    assert captured["kwargs"]["model"] == model
    assert captured["kwargs"]["prism_metadata"]["prism"]["provider"] == "bedrock"


def test_chatbot_marks_assistant_error_on_stream_failure(monkeypatch) -> None:
    store = FakeChatStore()
    app.state.chat_store = store
    app.state.prism_client = FakePrismClient()
    app.state.credential_store = type(
        "CredStore",
        (),
        {
            "get_default_credential_for_provider": lambda self, _: type(
                "Cred", (), {"provider": "openai", "secrets": {"api_key": "k"}, "metadata": {}}
            )()
        },
    )()

    def fake_build_agent(**kwargs):
        return FakeAgent([{"data": "partial"}], RuntimeError("provider blew up"))

    monkeypatch.setattr(chatbot_main, "_build_agent", fake_build_agent)
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
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["status"] == "error"


def test_chatbot_marks_assistant_error_on_empty_response(monkeypatch) -> None:
    store = FakeChatStore()
    app.state.chat_store = store
    app.state.prism_client = FakePrismClient()
    app.state.credential_store = type(
        "CredStore",
        (),
        {
            "get_default_credential_for_provider": lambda self, _: type(
                "Cred", (), {"provider": "openai", "secrets": {"api_key": "k"}, "metadata": {}}
            )()
        },
    )()

    def fake_build_agent(**kwargs):
        return FakeAgent([])

    monkeypatch.setattr(chatbot_main, "_build_agent", fake_build_agent)
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
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["status"] == "error"


def test_chatbot_returns_no_credential_without_creating_rows(monkeypatch) -> None:
    store = FakeChatStore()
    app.state.chat_store = store
    app.state.prism_client = FakePrismClient()
    app.state.credential_store = type(
        "CredStore", (), {"get_default_credential_for_provider": lambda self, _: None}
    )()
    monkeypatch.setattr(chatbot_main, "_build_agent", lambda **_: FakeAgent([{"data": "x"}]))
    client = TestClient(app)

    created = client.post("/v1/conversations", json={"model_default": "gpt-4o"})
    conversation_id = created.json()["conversation_id"]
    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "hi", "model": "gpt-4o"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "no_credential", "provider": "openai"}
    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["messages"]
    assert messages == []


def test_chatbot_persists_partial_on_cancelled_error(monkeypatch) -> None:
    store = FakeChatStore()
    app.state.chat_store = store
    app.state.prism_client = FakePrismClient()
    app.state.credential_store = type(
        "CredStore",
        (),
        {
            "get_default_credential_for_provider": lambda self, _: type(
                "Cred",
                (),
                {"provider": "openai", "secrets": {"api_key": "k"}, "metadata": {}},
            )()
        },
    )()

    def fake_build_agent(**kwargs):
        return FakeAgent([{"data": "partial"}], asyncio.CancelledError())

    monkeypatch.setattr(chatbot_main, "_build_agent", fake_build_agent)
    client = TestClient(app)
    created = client.post("/v1/conversations", json={"model_default": "gpt-4o"})
    conversation_id = created.json()["conversation_id"]
    client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "hi", "model": "gpt-4o"},
    )
    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["messages"]
    assert messages[1]["status"] == "cancelled"
    assert messages[1]["content"] == "partial"
