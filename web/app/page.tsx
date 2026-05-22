"use client";

import {
  FormEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

type Conversation = {
  id: string;
  model_default: string;
  updated_at: string;
  message_count: number;
};

type Message = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  thinking_trace?: string | null;
};

type ModelOption = {
  id: string;
  label: string;
  provider: string;
  source: "discovered" | "fallback";
  thinking_supported: boolean;
};

type Credentials = {
  openaiApiKey: string;
  anthropicApiKey: string;
  geminiApiKey: string;
  awsAccessKeyId: string;
  awsSecretAccessKey: string;
  awsSessionToken: string;
  awsRegion: string;
};

type SseEvent = { event: string; data: Record<string, unknown> };

const defaultCredentials: Credentials = {
  openaiApiKey: "",
  anthropicApiKey: "",
  geminiApiKey: "",
  awsAccessKeyId: "",
  awsSecretAccessKey: "",
  awsSessionToken: "",
  awsRegion: "us-west-2",
};

const fallbackModel: ModelOption = {
  id: "gpt-4o",
  label: "GPT 4O",
  provider: "openai",
  source: "fallback",
  thinking_supported: false,
};

async function* readSseStream(
  body: ReadableStream<Uint8Array>,
): AsyncIterableIterator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseSseBlock(block);
        if (parsed) yield parsed;
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseSseBlock(block: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

function credentialHeaders(credentials: Credentials): HeadersInit {
  const headers: Record<string, string> = {};
  if (credentials.openaiApiKey) headers["x-prism-openai-api-key"] = credentials.openaiApiKey;
  if (credentials.anthropicApiKey) {
    headers["x-prism-anthropic-api-key"] = credentials.anthropicApiKey;
  }
  if (credentials.geminiApiKey) headers["x-prism-gemini-api-key"] = credentials.geminiApiKey;
  if (credentials.awsAccessKeyId) {
    headers["x-prism-aws-access-key-id"] = credentials.awsAccessKeyId;
  }
  if (credentials.awsSecretAccessKey) {
    headers["x-prism-aws-secret-access-key"] = credentials.awsSecretAccessKey;
  }
  if (credentials.awsSessionToken) {
    headers["x-prism-aws-session-token"] = credentials.awsSessionToken;
  }
  if (credentials.awsRegion) headers["x-prism-aws-region"] = credentials.awsRegion;
  return headers;
}

function storedCredentials(): Credentials {
  if (typeof window === "undefined") return defaultCredentials;
  try {
    const stored = window.localStorage.getItem("prism.credentials");
    return stored ? { ...defaultCredentials, ...JSON.parse(stored) } : defaultCredentials;
  } catch {
    return defaultCredentials;
  }
}

export default function Home() {
  const apiUrl = useMemo(
    () => process.env.NEXT_PUBLIC_CHATBOT_API_URL ?? "http://localhost:8100",
    [],
  );
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [models, setModels] = useState<ModelOption[]>([fallbackModel]);
  const [model, setModel] = useState(fallbackModel.id);
  const [draft, setDraft] = useState("");
  const [isBusy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [modelStatus, setModelStatus] = useState<string | null>(null);
  const [showCredentials, setShowCredentials] = useState(false);
  const [credentials, setCredentials] = useState<Credentials>(storedCredentials);
  const messageListRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    window.localStorage.setItem("prism.credentials", JSON.stringify(credentials));
  }, [credentials]);

  const loadConversations = useCallback(async () => {
    try {
      const response = await fetch(`${apiUrl}/v1/conversations`);
      if (!response.ok) {
        setStatus("Unable to load conversations.");
        return;
      }
      const body = (await response.json()) as { conversations: Conversation[] };
      setConversations(body.conversations);
      if (!conversationId && body.conversations[0]) {
        setConversationId(body.conversations[0].id);
        setModel(body.conversations[0].model_default);
      }
    } catch {
      setStatus(`Chat API is not reachable at ${apiUrl}.`);
    }
  }, [apiUrl, conversationId]);

  const loadMessages = useCallback(
    async (id: string) => {
      try {
        const response = await fetch(`${apiUrl}/v1/conversations/${id}/messages`);
        if (!response.ok) {
          setStatus("Unable to load messages.");
          return;
        }
        const body = (await response.json()) as { messages: Message[] };
        setMessages(body.messages);
      } catch {
        setStatus(`Chat API is not reachable at ${apiUrl}.`);
      }
    },
    [apiUrl],
  );

  const loadModels = useCallback(async () => {
    setModelStatus("Discovering models...");
    try {
      const response = await fetch(`${apiUrl}/v1/models`, {
        headers: credentialHeaders(credentials),
      });
      if (!response.ok) throw new Error("Unable to discover models.");
      const body = (await response.json()) as {
        models: ModelOption[];
        discovery_errors: Record<string, string>;
      };
      const nextModels = body.models.length ? body.models : [fallbackModel];
      setModels(nextModels);
      setModel((current) =>
        nextModels.some((item) => item.id === current) ? current : nextModels[0].id,
      );
      const hasFallback = nextModels.some((item) => item.source === "fallback");
      const errorCount = Object.keys(body.discovery_errors).length;
      setModelStatus(
        hasFallback || errorCount
          ? "Using discovered models plus safe fallbacks."
          : "Model list discovered from active credentials.",
      );
    } catch (error) {
      setModels([fallbackModel]);
      setModel(fallbackModel.id);
      setModelStatus(
        error instanceof Error ? error.message : "Using fallback model list.",
      );
    }
  }, [apiUrl, credentials]);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    void loadModels();
    // Run once at startup; the credentials panel refresh button controls re-discovery.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiUrl]);

  useEffect(() => {
    if (conversationId) void loadMessages(conversationId);
  }, [conversationId, loadMessages]);

  useEffect(() => {
    const node = messageListRef.current;
    if (node) node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (["TEXTAREA", "INPUT", "SELECT"].includes(target?.tagName ?? "")) return;
      const node = messageListRef.current;
      if (!node) return;
      if (event.key === "ArrowDown") node.scrollBy({ top: 120, behavior: "smooth" });
      if (event.key === "ArrowUp") node.scrollBy({ top: -120, behavior: "smooth" });
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  async function createConversation() {
    setBusy(true);
    setStatus(null);
    try {
      const response = await fetch(`${apiUrl}/v1/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model_default: model,
          system_prompt: "You are a concise, practical assistant.",
        }),
      });
      if (!response.ok) throw new Error("create failed");
      const body = (await response.json()) as { conversation_id: string };
      setConversationId(body.conversation_id);
      setMessages([]);
      await loadConversations();
    } catch {
      setStatus("Unable to create a conversation.");
    } finally {
      setBusy(false);
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    if (!content) return;

    let activeConversationId = conversationId;
    setBusy(true);
    setStatus(null);
    try {
      if (!activeConversationId) {
        const response = await fetch(`${apiUrl}/v1/conversations`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            model_default: model,
            system_prompt: "You are a concise, practical assistant.",
          }),
        });
        if (!response.ok) throw new Error("create failed");
        const body = (await response.json()) as { conversation_id: string };
        activeConversationId = body.conversation_id;
        setConversationId(body.conversation_id);
      }

      setDraft("");
      const draftUserId = `local-user-${Date.now()}`;
      const draftAssistantId = `local-asst-${Date.now()}`;
      setMessages((current) => [
        ...current,
        {
          id: draftUserId,
          role: "user",
          content,
          created_at: new Date().toISOString(),
        },
        {
          id: draftAssistantId,
          role: "assistant",
          content: "",
          created_at: new Date().toISOString(),
          thinking_trace: "",
        },
      ]);

      const response = await fetch(
        `${apiUrl}/v1/conversations/${activeConversationId}/messages`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            ...credentialHeaders(credentials),
          },
          body: JSON.stringify({ role: "user", content, model }),
        },
      );
      if (!response.ok || !response.body) {
        const text = await response.text();
        throw new Error(text || "send failed");
      }

      let userId = draftUserId;
      let assistantId = draftAssistantId;
      let streamError: string | null = null;
      for await (const { event, data } of readSseStream(response.body)) {
        if (event === "user_message") {
          userId = data.id as string;
          setMessages((current) =>
            current.map((message) =>
              message.id === draftUserId
                ? { ...message, id: userId, created_at: data.created_at as string }
                : message,
            ),
          );
        } else if (event === "assistant_message") {
          assistantId = data.id as string;
          setMessages((current) =>
            current.map((message) =>
              message.id === draftAssistantId
                ? {
                    ...message,
                    id: assistantId,
                    created_at: data.created_at as string,
                  }
                : message,
            ),
          );
        } else if (event === "thinking") {
          const delta = (data.delta as string) ?? "";
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    thinking_trace: `${message.thinking_trace ?? ""}${delta}`,
                  }
                : message,
            ),
          );
        } else if (event === "token") {
          const delta = (data.delta as string) ?? "";
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? { ...message, content: message.content + delta }
                : message,
            ),
          );
        } else if (event === "error") {
          const detail = data.error as { message?: string } | undefined;
          streamError = detail?.message ?? "stream error";
        }
      }
      if (streamError) throw new Error(streamError);
      await loadConversations();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to send message.");
    } finally {
      setBusy(false);
    }
  }

  function handleDraftKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  const selectedModel = models.find((item) => item.id === model) ?? fallbackModel;
  const providerGroups = Array.from(
    models.reduce((groups, item) => {
      const group = groups.get(item.provider) ?? [];
      group.push(item);
      groups.set(item.provider, group);
      return groups;
    }, new Map<string, ModelOption[]>()),
  );

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">P</div>
          <div>
            <h1 className="brand-title">Prism</h1>
            <p className="brand-subtitle">LLM traces and chat</p>
          </div>
        </div>
        <button className="new-chat" disabled={isBusy} onClick={createConversation}>
          New chat
        </button>
        <div className="conversation-list">
          {conversations.map((conversation) => (
            <button
              className={`conversation-button ${
                conversation.id === conversationId ? "active" : ""
              }`}
              key={conversation.id}
              onClick={() => {
                setConversationId(conversation.id);
                setModel(conversation.model_default);
              }}
            >
              <span className="conversation-model">{conversation.model_default}</span>
              <span className="conversation-meta">
                {conversation.message_count} messages
              </span>
            </button>
          ))}
        </div>
      </aside>

      <section className="chat-pane">
        <header className="toolbar">
          <div>
            <h2 className="toolbar-title">Chat</h2>
            <p className="toolbar-subtitle">
              {selectedModel.provider}
              {selectedModel.thinking_supported ? " - thinking available" : ""}
            </p>
          </div>
          <div className="toolbar-actions">
            <button
              className="ghost-button"
              type="button"
              onClick={() => setShowCredentials((value) => !value)}
            >
              Credentials
            </button>
            <select
              className="model-select"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              aria-label="Model"
            >
              {providerGroups.map(([provider, items]) => (
                <optgroup key={provider} label={provider}>
                  {items.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                      {item.source === "fallback" ? " (fallback)" : ""}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>
        </header>

        {showCredentials ? (
          <section className="credential-panel">
            <input
              value={credentials.openaiApiKey}
              onChange={(event) =>
                setCredentials((current) => ({
                  ...current,
                  openaiApiKey: event.target.value,
                }))
              }
              placeholder="OpenAI API key"
              type="password"
            />
            <input
              value={credentials.anthropicApiKey}
              onChange={(event) =>
                setCredentials((current) => ({
                  ...current,
                  anthropicApiKey: event.target.value,
                }))
              }
              placeholder="Anthropic API key"
              type="password"
            />
            <input
              value={credentials.geminiApiKey}
              onChange={(event) =>
                setCredentials((current) => ({
                  ...current,
                  geminiApiKey: event.target.value,
                }))
              }
              placeholder="Gemini API key"
              type="password"
            />
            <input
              value={credentials.awsRegion}
              onChange={(event) =>
                setCredentials((current) => ({ ...current, awsRegion: event.target.value }))
              }
              placeholder="AWS region"
            />
            <input
              value={credentials.awsAccessKeyId}
              onChange={(event) =>
                setCredentials((current) => ({
                  ...current,
                  awsAccessKeyId: event.target.value,
                }))
              }
              placeholder="AWS access key id"
              type="password"
            />
            <input
              value={credentials.awsSecretAccessKey}
              onChange={(event) =>
                setCredentials((current) => ({
                  ...current,
                  awsSecretAccessKey: event.target.value,
                }))
              }
              placeholder="AWS secret access key"
              type="password"
            />
            <input
              value={credentials.awsSessionToken}
              onChange={(event) =>
                setCredentials((current) => ({
                  ...current,
                  awsSessionToken: event.target.value,
                }))
              }
              placeholder="AWS session token"
              type="password"
            />
            <button className="refresh-button" type="button" onClick={loadModels}>
              Refresh models
            </button>
            {modelStatus ? <span className="model-status">{modelStatus}</span> : null}
          </section>
        ) : null}

        <div className="message-list" ref={messageListRef} tabIndex={0}>
          {messages.length === 0 ? (
            <div className="empty-state">
              <span className="empty-glow" />
              <p>
                Start a conversation. Prism stores the chat, streams model output,
                and keeps thinking traces when the provider emits them.
              </p>
            </div>
          ) : (
            messages
              .filter((message) => message.role !== "system")
              .map((message) => (
                <div className={`message ${message.role}`} key={message.id}>
                  <div className="message-role">{message.role}</div>
                  {message.thinking_trace ? (
                    <details className="thinking-trace">
                      <summary>Thinking trace</summary>
                      <div>{message.thinking_trace}</div>
                    </details>
                  ) : null}
                  <div className="message-content">{message.content}</div>
                </div>
              ))
          )}
          {status ? <div className="status-line">{status}</div> : null}
        </div>

        <form className="composer" onSubmit={sendMessage}>
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleDraftKeyDown}
            placeholder="Send a message"
            aria-label="Message"
          />
          <button
            className="send-button"
            disabled={isBusy || draft.trim().length === 0}
          >
            {isBusy ? "Sending" : "Send"}
          </button>
        </form>
      </section>
    </main>
  );
}
