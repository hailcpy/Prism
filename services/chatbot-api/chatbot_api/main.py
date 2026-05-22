from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, cast

import litellm
import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

import prism_sdk
from prism_sdk import PrismClient

MessageRole = Literal["user", "assistant", "system"]


@dataclass(frozen=True)
class Conversation:
    id: str
    model_default: str
    system_prompt: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


@dataclass(frozen=True)
class Message:
    id: str
    conversation_id: str
    role: MessageRole
    content: str
    created_at: datetime


class ChatStore(Protocol):
    def create_conversation(
        self, model_default: str, system_prompt: str | None
    ) -> Conversation: ...

    def list_conversations(self, limit: int = 50) -> list[Conversation]: ...

    def get_conversation(self, conversation_id: str) -> Conversation | None: ...

    def list_messages(self, conversation_id: str) -> list[Message]: ...

    def create_message(self, conversation_id: str, role: MessageRole, content: str) -> Message: ...

    def update_message_content(self, message_id: str, content: str) -> Message: ...

    def delete_message(self, message_id: str) -> None: ...


class PostgresChatStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def create_conversation(self, model_default: str, system_prompt: str | None) -> Conversation:
        conversation_id = str(uuid.uuid4())
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO conversations (id, model_default, system_prompt)
                VALUES (%s, %s, %s)
                RETURNING id::text, model_default, system_prompt, created_at, updated_at
                """,
                (conversation_id, model_default, system_prompt),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("conversation insert returned no row")
            return _conversation_from_row(row)

    def list_conversations(self, limit: int = 50) -> list[Conversation]:
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT c.id::text, c.model_default, c.system_prompt, c.created_at, c.updated_at,
                       count(m.id)::int AS message_count
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                GROUP BY c.id
                ORDER BY c.updated_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [_conversation_from_row(row) for row in cur.fetchall()]

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id::text, model_default, system_prompt, created_at, updated_at,
                       0 AS message_count
                FROM conversations
                WHERE id = %s
                """,
                (conversation_id,),
            )
            row = cur.fetchone()
            return _conversation_from_row(row) if row else None

    def list_messages(self, conversation_id: str) -> list[Message]:
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id::text, conversation_id::text, role::text, content, created_at
                FROM messages
                WHERE conversation_id = %s
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            )
            return [_message_from_row(row) for row in cur.fetchall()]

    def create_message(self, conversation_id: str, role: MessageRole, content: str) -> Message:
        message_id = str(uuid.uuid4())
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO messages (id, conversation_id, role, content)
                VALUES (%s, %s, %s, %s)
                RETURNING id::text, conversation_id::text, role::text, content, created_at
                """,
                (message_id, conversation_id, role, content),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("message insert returned no row")
            message = _message_from_row(row)
            cur.execute(
                "UPDATE conversations SET updated_at = now() WHERE id = %s", (conversation_id,)
            )
            return message

    def update_message_content(self, message_id: str, content: str) -> Message:
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE messages
                SET content = %s
                WHERE id = %s
                RETURNING id::text, conversation_id::text, role::text, content, created_at
                """,
                (content, message_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(message_id)
            cur.execute(
                "UPDATE conversations SET updated_at = now() WHERE id = %s",
                (row["conversation_id"],),
            )
            return _message_from_row(row)

    def delete_message(self, message_id: str) -> None:
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM messages WHERE id = %s", (message_id,))


class CreateConversationRequest(BaseModel):
    model_default: str = "gpt-4o"
    system_prompt: str | None = None


class CreateConversationResponse(BaseModel):
    conversation_id: str
    created_at: datetime


class ConversationBody(BaseModel):
    id: str
    model_default: str
    updated_at: datetime
    message_count: int


class ListConversationsResponse(BaseModel):
    conversations: list[ConversationBody]
    next_cursor: str | None = None


class MessageBody(BaseModel):
    id: str
    role: MessageRole
    content: str
    created_at: datetime


class ListMessagesResponse(BaseModel):
    messages: list[MessageBody]


class SendMessageRequest(BaseModel):
    role: Literal["user"] = "user"
    content: str = Field(min_length=1)
    model: str | None = None


app = FastAPI(title="prism-chatbot-api", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "CORS_ALLOW_ORIGINS", "http://localhost:3000,http://localhost:3001"
    ).split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.state.chat_store = None
app.state.prism_client = None


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/conversations", status_code=201, response_model=CreateConversationResponse)
def create_conversation(
    request: Request, body: CreateConversationRequest
) -> CreateConversationResponse:
    conversation = _get_store(request.app).create_conversation(
        body.model_default, body.system_prompt
    )
    return CreateConversationResponse(
        conversation_id=conversation.id, created_at=conversation.created_at
    )


@app.get("/v1/conversations", response_model=ListConversationsResponse)
def list_conversations(request: Request) -> ListConversationsResponse:
    conversations = _get_store(request.app).list_conversations()
    return ListConversationsResponse(
        conversations=[
            ConversationBody(
                id=conversation.id,
                model_default=conversation.model_default,
                updated_at=conversation.updated_at,
                message_count=conversation.message_count,
            )
            for conversation in conversations
        ]
    )


@app.get("/v1/conversations/{conversation_id}/messages", response_model=ListMessagesResponse)
def list_messages(request: Request, conversation_id: str) -> ListMessagesResponse:
    store = _get_store(request.app)
    if store.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ListMessagesResponse(
        messages=[_message_body(message) for message in store.list_messages(conversation_id)]
    )


@app.post("/v1/conversations/{conversation_id}/messages")
async def send_message(
    request: Request,
    conversation_id: str,
    body: SendMessageRequest,
) -> StreamingResponse:
    store = _get_store(request.app)
    conversation = store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    user_message = store.create_message(conversation_id, "user", body.content)
    assistant_message = store.create_message(conversation_id, "assistant", "")
    model = body.model or conversation.model_default
    messages = _llm_messages(
        system_prompt=conversation.system_prompt,
        history=store.list_messages(conversation_id),
    )
    _get_prism_client(request.app)
    inference_id = str(uuid.uuid4())
    prism_metadata = prism_sdk.metadata(
        conversation_id=conversation_id,
        message_id=assistant_message.id,
        inference_id=inference_id,
        extra={"source": "chatbot-api"},
    )

    async def event_stream() -> AsyncIterator[bytes]:
        yield _sse(
            "user_message",
            {
                "id": user_message.id,
                "role": "user",
                "content": user_message.content,
                "created_at": user_message.created_at.isoformat(),
            },
        )
        yield _sse(
            "assistant_message",
            {
                "id": assistant_message.id,
                "role": "assistant",
                "created_at": assistant_message.created_at.isoformat(),
            },
        )
        collected: list[str] = []
        try:
            stream = await litellm.acompletion(
                model=model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                metadata=prism_metadata,
            )
            async for chunk in stream:
                delta = _stream_delta(chunk)
                if delta:
                    collected.append(delta)
                    yield _sse("token", {"delta": delta})
        except (Exception, asyncio.CancelledError) as exc:
            store.delete_message(assistant_message.id)
            yield _sse(
                "error",
                {"error": {"type": type(exc).__name__, "message": str(exc)}},
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
            return
        content = "".join(collected)
        if not content:
            store.delete_message(assistant_message.id)
            yield _sse(
                "error",
                {"error": {"type": "EmptyResponse", "message": "model returned no content"}},
            )
            return
        store.update_message_content(assistant_message.id, content)
        yield _sse(
            "done",
            {
                "message_id": assistant_message.id,
                "inference_id": inference_id,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _stream_delta(chunk: Any) -> str:
    if isinstance(chunk, dict):
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        return str(delta.get("content") or "")
    choices = getattr(chunk, "choices", []) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return ""
    content = getattr(delta, "content", None)
    return str(content or "")


def _llm_messages(*, system_prompt: str | None, history: list[Message]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for message in history[-12:]:
        if message.role in {"user", "assistant"} and message.content:
            messages.append({"role": message.role, "content": message.content})
    return messages


def _get_store(app: FastAPI) -> ChatStore:
    if app.state.chat_store is None:
        app.state.chat_store = PostgresChatStore(os.environ["DATABASE_URL"])
    return app.state.chat_store


def _get_prism_client(app: FastAPI) -> PrismClient:
    if app.state.prism_client is None:
        sink = os.getenv("PRISM_SDK_SINK", "http")
        client = PrismClient(
            ingestion_url=os.getenv("INGESTION_URL", "http://localhost:8001"),
            sink=cast(Literal["http", "noop", "stdout"], sink),
        )
        client.install()
        app.state.prism_client = client
    return app.state.prism_client


def _conversation_from_row(row: dict[str, Any]) -> Conversation:
    return Conversation(
        id=row["id"],
        model_default=row["model_default"],
        system_prompt=row["system_prompt"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        message_count=row.get("message_count", 0),
    )


def _message_from_row(row: dict[str, Any]) -> Message:
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
    )


def _message_body(message: Message) -> MessageBody:
    return MessageBody(
        id=message.id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
    )
