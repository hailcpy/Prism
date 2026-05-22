"use client";

import {
  FormEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  Conversation,
  Message,
  ModelOption,
  apiUrl,
  createConversation,
  getConversations,
  getMessages,
  getModels,
  readSseStream,
} from "@/lib/api";

const fallbackModel: ModelOption = {
  id: "gpt-4o",
  label: "GPT 4O",
  provider: "openai",
  source: "fallback",
  thinking_supported: false,
};

export default function Home() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [model, setModel] = useState<string>(fallbackModel.id);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [modelStatus, setModelStatus] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);

  const loadConversations = useCallback(async () => {
    try {
      setConversations(await getConversations());
    } catch (e) {
      setStatus(
        e instanceof Error ? e.message : "failed to load conversations",
      );
    }
  }, []);

  const loadModels = useCallback(async () => {
    try {
      setModelStatus("Refreshing…");
      const list = await getModels();
      setModels(list);
      setModel((current) =>
        list.find((m) => m.id === current) ? current : (list[0]?.id ?? current),
      );
      const fallbackCount = list.filter((m) => m.source === "fallback").length;
      setModelStatus(
        fallbackCount === list.length && list.length > 0
          ? "No live models — add a credential in Settings"
          : `${list.length} model${list.length === 1 ? "" : "s"} loaded`,
      );
    } catch (e) {
      setModelStatus(e instanceof Error ? e.message : "failed to load models");
    }
  }, []);

  useEffect(() => {
    void loadConversations();
    void loadModels();
  }, [loadConversations, loadModels]);

  useEffect(() => {
    function onVis() {
      if (document.visibilityState === "visible") void loadModels();
    }
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("focus", onVis);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("focus", onVis);
    };
  }, [loadModels]);

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      return;
    }
    void getMessages(conversationId)
      .then(setMessages)
      .catch((e) => {
        setStatus(e instanceof Error ? e.message : "failed to load messages");
      });
  }, [conversationId]);

  useEffect(() => {
    const el = messageListRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  useEffect(() => {
    function onEsc(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") abortRef.current?.abort();
    }
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, []);

  async function startNewChat() {
    setConversationId(null);
    setMessages([]);
    setStatus("");
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (!content) return;
    setBusy(true);
    setStatus("");
    let activeId = conversationId;
    if (!activeId) {
      try {
        activeId = await createConversation(model);
        setConversationId(activeId);
      } catch (e) {
        setStatus(
          e instanceof Error ? e.message : "failed to create conversation",
        );
        setBusy(false);
        return;
      }
    }
    const now = new Date().toISOString();
    setMessages((current) => [
      ...current,
      {
        id: `local-user-${Date.now()}`,
        role: "user",
        status: "ok",
        content,
        created_at: now,
      },
    ]);
    setDraft("");
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const response = await fetch(
        `${apiUrl}/v1/conversations/${activeId}/messages`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          },
          body: JSON.stringify({ role: "user", content, model }),
          signal: controller.signal,
        },
      );
      if (!response.ok || !response.body) {
        setStatus((await response.text()) || "request failed");
        return;
      }
      for await (const { event: name, data } of readSseStream(response.body)) {
        if (name === "token") {
          const delta = String(data.delta ?? "");
          setMessages((current) => {
            const next = [...current];
            const last = next[next.length - 1];
            if (last?.role === "assistant" && last.status === "pending") {
              next[next.length - 1] = {
                ...last,
                content: last.content + delta,
              };
            } else {
              next.push({
                id: `local-${Date.now()}`,
                role: "assistant",
                status: "pending",
                content: delta,
                created_at: new Date().toISOString(),
              });
            }
            return next;
          });
        } else if (name === "done") {
          await getMessages(activeId).then(setMessages);
        } else if (name === "error") {
          const detail = data.error as { message?: string } | undefined;
          setStatus(detail?.message ?? "stream error");
        }
      }
      await getMessages(activeId).then(setMessages);
      await loadConversations();
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setStatus(e instanceof Error ? e.message : "send failed");
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  function handleKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  const selected = models.find((m) => m.id === model) ?? fallbackModel;
  const providerGroups = Array.from(
    models.reduce((groups, item) => {
      const list = groups.get(item.provider) ?? [];
      list.push(item);
      groups.set(item.provider, list);
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
        <button
          className="new-chat"
          disabled={busy}
          onClick={() => void startNewChat()}
        >
          New chat
        </button>
        <div className="conversation-list">
          {conversations.map((c) => (
            <button
              key={c.id}
              className={`conversation-button ${
                c.id === conversationId ? "active" : ""
              }`}
              onClick={() => {
                setConversationId(c.id);
                setModel(c.model_default);
              }}
            >
              <span className="conversation-model">{c.model_default}</span>
              <span className="conversation-meta">
                {c.message_count} messages
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
              {selected.provider}
              {selected.thinking_supported ? " · thinking available" : ""}
              {selected.source === "fallback" ? " · fallback" : ""}
            </p>
          </div>
          <div className="toolbar-actions">
            {modelStatus ? (
              <span className="model-status">{modelStatus}</span>
            ) : null}
            <button
              type="button"
              className="refresh-button"
              onClick={() => void loadModels()}
            >
              Refresh
            </button>
            <select
              className="model-select"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              aria-label="Model"
            >
              {providerGroups.length === 0 ? (
                <option value={fallbackModel.id}>{fallbackModel.label}</option>
              ) : (
                providerGroups.map(([provider, items]) => (
                  <optgroup key={provider} label={provider}>
                    {items.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}
                        {item.source === "fallback" ? " (fallback)" : ""}
                      </option>
                    ))}
                  </optgroup>
                ))
              )}
            </select>
          </div>
        </header>

        <div />

        <div className="message-list" ref={messageListRef} tabIndex={0}>
          {messages.length === 0 ? (
            <div className="empty-state">
              <span className="empty-glow" />
              <p>
                Start a conversation. Prism stores the chat, streams model
                output, and keeps thinking traces when the provider emits them.
              </p>
            </div>
          ) : (
            messages
              .filter((m) => m.role !== "system")
              .map((m) => (
                <div className={`message ${m.role}`} key={m.id}>
                  <div className="message-role">{m.role}</div>
                  {m.thinking_trace ? (
                    <details className="thinking-trace">
                      <summary>Thinking trace</summary>
                      <div>{m.thinking_trace}</div>
                    </details>
                  ) : null}
                  <div className="message-content">{m.content}</div>
                </div>
              ))
          )}
          {status ? <div className="status-line">{status}</div> : null}
        </div>

        <form className="composer" onSubmit={send}>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Send a message — Enter to send, Shift+Enter for newline"
            aria-label="Message"
          />
          <button
            className="send-button"
            disabled={busy || draft.trim().length === 0}
          >
            {busy ? "Sending" : "Send"}
          </button>
        </form>
      </section>
    </main>
  );
}
