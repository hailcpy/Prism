from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast

import litellm
import psycopg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from strands import Agent, tool
from strands.models.litellm import LiteLLMModel

import prism_sdk
from prism_infra.models import MetricsQuery
from prism_infra.storage import LogStore, PostgresLogStore
from prism_sdk import PrismClient
from prism_sdk.strands import PrismStrandsHooks

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


@tool
def now() -> str:
    """Return the current UTC time."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@tool
def web_search(query: str) -> str:
    """Search the web for a query using a deterministic demo stub."""
    return f"Demo search result for {query!r}: no live web request was made."


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
app.state.log_store = None


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

    previous_messages = store.list_messages(conversation_id)
    user_message = store.create_message(conversation_id, "user", body.content)
    assistant_message = store.create_message(conversation_id, "assistant", "")
    model, provider = _litellm_model_and_provider(body.model or conversation.model_default)
    prism_client = _get_prism_client(request.app)
    inference_id = str(uuid.uuid4())
    prism_metadata = prism_sdk.metadata(
        conversation_id=conversation_id,
        message_id=assistant_message.id,
        inference_id=inference_id,
        provider=provider,
        extra={"source": "chatbot-api"},
    )
    agent = _build_agent(
        model=model,
        system_prompt=conversation.system_prompt,
        history=previous_messages,
        prism_metadata=prism_metadata,
        prism_client=prism_client,
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
            stream = agent.stream_async(
                body.content,
                invocation_state={
                    "prism_conversation_id": conversation_id,
                    "prism_inference_id": inference_id,
                    "prism_metadata": {"source": "chatbot-api"},
                },
            )
            async for event in stream:
                delta = _agent_stream_delta(event)
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


def _agent_stream_delta(event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    data = event.get("data")
    if isinstance(data, str):
        return data
    if event.get("type") != "modelStreamUpdateEvent":
        return ""
    inner = event.get("event")
    if not isinstance(inner, dict):
        return ""
    if inner.get("type") != "modelContentBlockDeltaEvent":
        return ""
    delta = inner.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "textDelta":
        return ""
    return str(delta.get("text") or "")


def _llm_messages(*, system_prompt: str | None, history: list[Message]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for message in history[-12:]:
        if message.role in {"user", "assistant"} and message.content:
            messages.append({"role": message.role, "content": message.content})
    return messages


def _build_agent(
    *,
    model: str,
    system_prompt: str | None,
    history: list[Message],
    prism_metadata: dict[str, Any],
    prism_client: PrismClient,
) -> Agent:
    llm = LiteLLMModel(
        client_args={
            "metadata": prism_metadata,
            "stream_options": {"include_usage": True},
        },
        model_id=model,
        params={"stream": True},
    )
    strands_history = cast(Any, _strands_messages(history))
    return Agent(
        model=llm,
        messages=strands_history,
        tools=[now, web_search],
        system_prompt=system_prompt,
        callback_handler=None,
        hooks=[PrismStrandsHooks(prism_client)],
    )


def _strands_messages(history: list[Message]) -> list[dict[str, Any]]:
    return [
        {"role": message.role, "content": [{"text": message.content}]}
        for message in history[-12:]
        if message.role in {"user", "assistant"} and message.content
    ]


def _litellm_model_and_provider(model: str) -> tuple[str, str]:
    if model.startswith("bedrock/arn:"):
        model = f"bedrock/converse/{model.removeprefix('bedrock/')}"
    _, provider, _, _ = litellm.get_llm_provider(model)
    return model, provider


def _get_store(app: FastAPI) -> ChatStore:
    if app.state.chat_store is None:
        app.state.chat_store = PostgresChatStore(os.environ["DATABASE_URL"])
    return app.state.chat_store


class MetricsBucket(BaseModel):
    minute_bucket: datetime
    model: str
    provider: str
    count: int
    error_count: int
    latency_p50_ms: int
    latency_p95_ms: int
    prompt_tokens_sum: int
    completion_tokens_sum: int


class MetricsResponse(BaseModel):
    buckets: list[MetricsBucket]


@app.get("/v1/metrics", response_model=MetricsResponse)
def get_metrics(
    request: Request,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    model: list[str] | None = Query(default=None),
    provider: list[str] | None = Query(default=None),
    interval: str = Query(default="1m"),
) -> MetricsResponse:
    if interval != "1m":
        raise HTTPException(status_code=400, detail="only interval=1m is supported")
    end = to or datetime.now(UTC)
    start = from_ or end - timedelta(hours=1)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    rows = _get_log_store(request.app).get_metrics(
        MetricsQuery(
            start=start,
            end=end,
            models=tuple(model or ()),
            providers=tuple(provider or ()),
        )
    )
    return MetricsResponse(
        buckets=[
            MetricsBucket(
                minute_bucket=row.minute_bucket,
                model=row.model,
                provider=row.provider,
                count=row.count,
                error_count=row.error_count,
                latency_p50_ms=row.latency_p50_ms,
                latency_p95_ms=row.latency_p95_ms,
                prompt_tokens_sum=row.prompt_tokens_sum,
                completion_tokens_sum=row.completion_tokens_sum,
            )
            for row in rows
        ]
    )


def _get_log_store(app: FastAPI) -> LogStore:
    if app.state.log_store is None:
        app.state.log_store = PostgresLogStore(os.environ["DATABASE_URL"])
    return app.state.log_store


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
