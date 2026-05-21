from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi.testclient import TestClient

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


class FakePrismClient:
    def __init__(self) -> None:
        self.chat = FakeChat()
        self.calls: list[dict[str, Any]] = []
        self.chat.completions.client = self


class FakeChat:
    def __init__(self) -> None:
        self.completions = FakeCompletions()


class FakeCompletions:
    client: FakePrismClient

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.client.calls.append(kwargs)
        return {"choices": [{"message": {"content": "hello from model"}}]}


def test_chatbot_creates_conversation_and_sends_message() -> None:
    store = FakeChatStore()
    prism_client = FakePrismClient()
    app.state.chat_store = store
    app.state.prism_client = prism_client
    client = TestClient(app)

    created = client.post("/v1/conversations", json={"model_default": "gpt-4o"})
    conversation_id = created.json()["conversation_id"]
    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"role": "user", "content": "hello", "model": "gpt-4o"},
    )

    assert response.status_code == 200
    assert response.json()["assistant_message"]["content"] == "hello from model"
    assert prism_client.calls[0]["conversation_id"] == conversation_id
    assert response.json()["assistant_message"]["id"] == prism_client.calls[0]["message_id"]

    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
