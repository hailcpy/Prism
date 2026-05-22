from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast

import boto3
import httpx
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
from chatbot_api.dashboards import router as dashboards_router
from prism_infra.models import MetricsQuery
from prism_infra.storage import LogStore, PostgresLogStore, run_migrations
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
    metadata: dict[str, Any] | None = None


class ChatStore(Protocol):
    def create_conversation(
        self, model_default: str, system_prompt: str | None
    ) -> Conversation: ...

    def list_conversations(self, limit: int = 50) -> list[Conversation]: ...

    def get_conversation(self, conversation_id: str) -> Conversation | None: ...

    def list_messages(self, conversation_id: str) -> list[Message]: ...

    def create_message(
        self,
        conversation_id: str,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message: ...

    def update_message_content(
        self,
        message_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message: ...

    def delete_message(self, message_id: str) -> None: ...


class PostgresChatStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._ensured_schema = False

    def _ensure_schema(self) -> None:
        if self._ensured_schema:
            return
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE messages ADD COLUMN IF NOT EXISTS metadata_jsonb JSONB "
                "NOT NULL DEFAULT '{}'::jsonb"
            )
        self._ensured_schema = True

    def create_conversation(self, model_default: str, system_prompt: str | None) -> Conversation:
        self._ensure_schema()
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
        self._ensure_schema()
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
        self._ensure_schema()
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
        self._ensure_schema()
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id::text, conversation_id::text, role::text, content, created_at,
                       metadata_jsonb
                FROM messages
                WHERE conversation_id = %s
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            )
            return [_message_from_row(row) for row in cur.fetchall()]

    def create_message(
        self,
        conversation_id: str,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        self._ensure_schema()
        message_id = str(uuid.uuid4())
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO messages (id, conversation_id, role, content, metadata_jsonb)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id::text, conversation_id::text, role::text, content, created_at,
                          metadata_jsonb
                """,
                (message_id, conversation_id, role, content, json.dumps(metadata or {})),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("message insert returned no row")
            message = _message_from_row(row)
            cur.execute(
                "UPDATE conversations SET updated_at = now() WHERE id = %s", (conversation_id,)
            )
            return message

    def update_message_content(
        self,
        message_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        self._ensure_schema()
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            params: tuple[Any, ...]
            metadata_sql = ""
            if metadata is not None:
                metadata_sql = ", metadata_jsonb = %s"
                params = (content, json.dumps(metadata), message_id)
            else:
                params = (content, message_id)
            cur.execute(
                f"""
                UPDATE messages
                SET content = %s{metadata_sql}
                WHERE id = %s
                RETURNING id::text, conversation_id::text, role::text, content, created_at,
                          metadata_jsonb
                """,
                params,
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
    thinking_trace: str | None = None


class ListMessagesResponse(BaseModel):
    messages: list[MessageBody]


class SendMessageRequest(BaseModel):
    role: Literal["user"] = "user"
    content: str = Field(min_length=1)
    model: str | None = None


class ModelOption(BaseModel):
    id: str
    label: str
    provider: str
    source: Literal["discovered", "fallback"]
    thinking_supported: bool = False


class ListModelsResponse(BaseModel):
    models: list[ModelOption]
    discovery_errors: dict[str, str] = Field(default_factory=dict)


@tool
def now() -> str:
    """Return the current UTC time."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@tool
def web_search(query: str) -> str:
    """Search the web for a query using a deterministic demo stub."""
    return f"Demo search result for {query!r}: no live web request was made."


@contextlib.asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            await asyncio.to_thread(run_migrations, database_url)
        except Exception:
            logging.getLogger(__name__).exception("startup migrations failed")
    yield


app = FastAPI(title="prism-chatbot-api", version="0.1.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:3000,http://localhost:3001,http://localhost:3002,"
        "http://127.0.0.1:3000,http://127.0.0.1:3001,http://127.0.0.1:3002",
    ).split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.state.chat_store = None
app.state.prism_client = None
app.state.log_store = None
app.state.dashboard_store = None
app.include_router(dashboards_router)


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models", response_model=ListModelsResponse)
async def list_models(request: Request) -> ListModelsResponse:
    credentials = _credentials_from_request(request)
    return await _discover_models(credentials)


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
    credentials = _credentials_from_request(request)
    agent = _build_agent(
        model=model,
        system_prompt=conversation.system_prompt,
        history=previous_messages,
        prism_metadata=prism_metadata,
        prism_client=prism_client,
        credentials=credentials,
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
        thinking_collected: list[str] = []
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
                thinking_delta = _agent_stream_thinking_delta(event)
                if thinking_delta:
                    thinking_collected.append(thinking_delta)
                    yield _sse("thinking", {"delta": thinking_delta})
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
        thinking_trace = "".join(thinking_collected).strip() or None
        metadata = {"thinking_trace": thinking_trace} if thinking_trace else {}
        store.update_message_content(assistant_message.id, content, metadata)
        yield _sse(
            "done",
            {
                "message_id": assistant_message.id,
                "inference_id": inference_id,
                "thinking_trace": thinking_trace,
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


def _agent_stream_thinking_delta(event: Any) -> str:
    if not isinstance(event, dict):
        return ""

    direct = _first_text_value(
        event,
        {
            "thinking",
            "thinking_delta",
            "reasoning",
            "reasoning_delta",
            "reasoning_content",
            "reasoning_text",
        },
    )
    if direct:
        return direct

    inner = event.get("event")
    if not isinstance(inner, dict):
        return ""
    delta = inner.get("delta")
    if not isinstance(delta, dict):
        return ""
    if str(delta.get("type") or "").lower() not in {
        "thinkingdelta",
        "reasoningdelta",
        "reasoning_delta",
        "thinking_delta",
    }:
        return ""
    return _first_text_value(delta, {"text", "thinking", "reasoning"}) or ""


def _first_text_value(value: Any, keys: set[str]) -> str:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in keys and isinstance(nested, str):
                return nested
            found = _first_text_value(nested, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _first_text_value(item, keys)
            if found:
                return found
    return ""


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
    credentials: dict[str, str] | None = None,
) -> Agent:
    client_args: dict[str, Any] = {
        "metadata": prism_metadata,
    }
    _, provider, _, _ = litellm.get_llm_provider(model)
    client_args.update(_litellm_client_args(credentials or {}, provider))
    llm = LiteLLMModel(
        client_args=client_args,
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


def _credentials_from_request(request: Request) -> dict[str, str]:
    header_to_key = {
        "x-prism-openai-api-key": "openai_api_key",
        "x-prism-anthropic-api-key": "anthropic_api_key",
        "x-prism-gemini-api-key": "gemini_api_key",
        "x-prism-aws-access-key-id": "aws_access_key_id",
        "x-prism-aws-secret-access-key": "aws_secret_access_key",
        "x-prism-aws-session-token": "aws_session_token",
        "x-prism-aws-region": "aws_region",
    }
    credentials: dict[str, str] = {}
    for header, key in header_to_key.items():
        value = request.headers.get(header)
        if value:
            credentials[key] = value.strip()
    return credentials


def _litellm_client_args(credentials: dict[str, str], provider: str) -> dict[str, Any]:
    client_args: dict[str, Any] = {}
    if provider == "openai" and credentials.get("openai_api_key"):
        client_args["api_key"] = credentials["openai_api_key"]
    if provider == "anthropic" and credentials.get("anthropic_api_key"):
        client_args["api_key"] = credentials["anthropic_api_key"]
    if provider in {"gemini", "google"} and credentials.get("gemini_api_key"):
        client_args["api_key"] = credentials["gemini_api_key"]
    if provider == "bedrock" and credentials.get("aws_access_key_id"):
        client_args["aws_access_key_id"] = credentials["aws_access_key_id"]
    if provider == "bedrock" and credentials.get("aws_secret_access_key"):
        client_args["aws_secret_access_key"] = credentials["aws_secret_access_key"]
    if provider == "bedrock" and credentials.get("aws_session_token"):
        client_args["aws_session_token"] = credentials["aws_session_token"]
    if provider == "bedrock" and credentials.get("aws_region"):
        client_args["aws_region_name"] = credentials["aws_region"]
    return client_args


async def _discover_models(credentials: dict[str, str]) -> ListModelsResponse:
    discovered: list[ModelOption] = []
    errors: dict[str, str] = {}

    async with httpx.AsyncClient(timeout=8.0) as client:
        if api_key := credentials.get("openai_api_key") or os.getenv("OPENAI_API_KEY"):
            try:
                discovered.extend(await _discover_openai_models(client, api_key))
            except Exception as exc:  # pragma: no cover - network/provider failure path
                errors["openai"] = str(exc)
        if api_key := credentials.get("anthropic_api_key") or os.getenv("ANTHROPIC_API_KEY"):
            try:
                discovered.extend(await _discover_anthropic_models(client, api_key))
            except Exception as exc:  # pragma: no cover - network/provider failure path
                errors["anthropic"] = str(exc)
        if api_key := credentials.get("gemini_api_key") or os.getenv("GEMINI_API_KEY"):
            try:
                discovered.extend(await _discover_gemini_models(client, api_key))
            except Exception as exc:  # pragma: no cover - network/provider failure path
                errors["gemini"] = str(exc)

    try:
        discovered.extend(_discover_bedrock_models(credentials))
    except Exception as exc:  # pragma: no cover - network/provider failure path
        errors["bedrock"] = str(exc)

    models = _dedupe_models(discovered)
    if not models:
        models = _fallback_models()
    return ListModelsResponse(models=models, discovery_errors=errors)


async def _discover_openai_models(client: httpx.AsyncClient, api_key: str) -> list[ModelOption]:
    response = await client.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    response.raise_for_status()
    ids = sorted(
        item["id"]
        for item in response.json().get("data", [])
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"].startswith(("gpt-", "o1", "o3", "o4", "o5", "chatgpt-"))
    )
    preferred = _prioritize(ids, ("gpt-4.1", "gpt-4o", "o3", "o4", "gpt-5"))
    return [
        ModelOption(
            id=model_id,
            label=_model_label(model_id),
            provider="openai",
            source="discovered",
            thinking_supported=model_id.startswith(("o", "gpt-5")),
        )
        for model_id in preferred[:40]
    ]


async def _discover_anthropic_models(client: httpx.AsyncClient, api_key: str) -> list[ModelOption]:
    response = await client.get(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    response.raise_for_status()
    ids = sorted(
        item["id"]
        for item in response.json().get("data", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    )
    return [
        ModelOption(
            id=model_id,
            label=_model_label(model_id),
            provider="anthropic",
            source="discovered",
            thinking_supported="claude" in model_id and "3-" not in model_id,
        )
        for model_id in ids[:40]
    ]


async def _discover_gemini_models(client: httpx.AsyncClient, api_key: str) -> list[ModelOption]:
    response = await client.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key},
    )
    response.raise_for_status()
    options: list[ModelOption] = []
    for item in response.json().get("models", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").removeprefix("models/")
        methods = item.get("supportedGenerationMethods") or []
        if not name or "generateContent" not in methods:
            continue
        model_id = f"gemini/{name}"
        options.append(
            ModelOption(
                id=model_id,
                label=_model_label(name),
                provider="gemini",
                source="discovered",
                thinking_supported="2.5" in name,
            )
        )
    return options[:40]


def _discover_bedrock_models(credentials: dict[str, str]) -> list[ModelOption]:
    if not (
        credentials.get("aws_access_key_id")
        or os.getenv("AWS_ACCESS_KEY_ID")
        or os.getenv("AWS_PROFILE")
    ):
        return []
    region = (
        credentials.get("aws_region")
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION", "us-west-2")
    )
    kwargs: dict[str, str] = {"region_name": region}
    if credentials.get("aws_access_key_id"):
        kwargs["aws_access_key_id"] = credentials["aws_access_key_id"]
    if credentials.get("aws_secret_access_key"):
        kwargs["aws_secret_access_key"] = credentials["aws_secret_access_key"]
    if credentials.get("aws_session_token"):
        kwargs["aws_session_token"] = credentials["aws_session_token"]
    client = boto3.client("bedrock", **kwargs)
    response = client.list_foundation_models()
    options: list[ModelOption] = []
    for summary in response.get("modelSummaries", []):
        model_id = summary.get("modelId")
        modalities = summary.get("outputModalities") or []
        if not isinstance(model_id, str) or "TEXT" not in modalities:
            continue
        lite_id = f"bedrock/{model_id}"
        options.append(
            ModelOption(
                id=lite_id,
                label=_model_label(model_id),
                provider="bedrock",
                source="discovered",
                thinking_supported="claude" in model_id and "3-" not in model_id,
            )
        )
    return sorted(options, key=lambda item: item.id)[:80]


def _fallback_models() -> list[ModelOption]:
    fallback = [
        ("gpt-4o", "openai", False),
        ("gpt-4.1", "openai", False),
        ("o3-mini", "openai", True),
        ("claude-3-5-sonnet-latest", "anthropic", False),
        ("claude-sonnet-4-5", "anthropic", True),
        ("gemini/gemini-1.5-flash", "gemini", False),
        ("gemini/gemini-2.5-flash", "gemini", True),
        ("bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0", "bedrock", True),
        ("bedrock/us.meta.llama3-3-70b-instruct-v1:0", "bedrock", False),
        ("bedrock/us.deepseek.r1-v1:0", "bedrock", True),
    ]
    return [
        ModelOption(
            id=model_id,
            label=_model_label(model_id),
            provider=provider,
            source="fallback",
            thinking_supported=thinking,
        )
        for model_id, provider, thinking in fallback
    ]


def _dedupe_models(models: list[ModelOption]) -> list[ModelOption]:
    by_id: dict[str, ModelOption] = {}
    for model in models:
        by_id.setdefault(model.id, model)
    if by_id:
        for fallback in _fallback_models():
            if fallback.provider in {model.provider for model in by_id.values()}:
                by_id.setdefault(fallback.id, fallback)
    return sorted(by_id.values(), key=lambda item: (item.provider, item.label))


def _prioritize(values: list[str], prefixes: tuple[str, ...]) -> list[str]:
    preferred = [value for value in values if value.startswith(prefixes)]
    rest = [value for value in values if value not in set(preferred)]
    return preferred + rest


def _model_label(model_id: str) -> str:
    base = model_id.removeprefix("bedrock/").removeprefix("gemini/")
    base = base.split("/")[-1]
    return base.replace(".", " ").replace("-", " ").replace("_", " ").title()


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
    cost_usd_sum: float = 0.0


class MetricsResponse(BaseModel):
    buckets: list[MetricsBucket]


class ConversationCostResponse(BaseModel):
    conversation_id: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int
    reasoning_tokens: int
    cost_usd: float


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
                cost_usd_sum=row.cost_usd_sum,
            )
            for row in rows
        ]
    )


@app.get(
    "/v1/conversations/{conversation_id}/cost",
    response_model=ConversationCostResponse,
)
def get_conversation_cost(request: Request, conversation_id: str) -> ConversationCostResponse:
    store = _get_store(request.app)
    if store.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    cost = _get_log_store(request.app).get_conversation_cost(conversation_id)
    return ConversationCostResponse(
        conversation_id=cost.conversation_id,
        calls=cost.calls,
        prompt_tokens=cost.prompt_tokens,
        completion_tokens=cost.completion_tokens,
        cached_prompt_tokens=cost.cached_prompt_tokens,
        reasoning_tokens=cost.reasoning_tokens,
        cost_usd=cost.cost_usd,
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
    metadata = row.get("metadata_jsonb") or {}
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


def _message_body(message: Message) -> MessageBody:
    metadata = message.metadata or {}
    thinking_trace = metadata.get("thinking_trace")
    return MessageBody(
        id=message.id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
        thinking_trace=thinking_trace if isinstance(thinking_trace, str) else None,
    )
