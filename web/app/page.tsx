"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

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
};

const models = [
  { label: "GPT-4o", value: "gpt-4o" },
  { label: "Claude Sonnet", value: "claude-3-5-sonnet-latest" },
  { label: "Gemini 1.5 Flash", value: "gemini/gemini-1.5-flash" },
];

export default function Home() {
  const apiUrl = useMemo(
    () => process.env.NEXT_PUBLIC_CHATBOT_API_URL ?? "http://localhost:8100",
    [],
  );
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [model, setModel] = useState(models[0].value);
  const [draft, setDraft] = useState("");
  const [isBusy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const loadConversations = useCallback(async () => {
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
  }, [apiUrl, conversationId]);

  const loadMessages = useCallback(
    async (id: string) => {
      const response = await fetch(`${apiUrl}/v1/conversations/${id}/messages`);
      if (!response.ok) {
        setStatus("Unable to load messages.");
        return;
      }
      const body = (await response.json()) as { messages: Message[] };
      setMessages(body.messages);
    },
    [apiUrl],
  );

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    if (conversationId) {
      void loadMessages(conversationId);
    }
  }, [conversationId, loadMessages]);

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
      if (!response.ok) {
        throw new Error("create failed");
      }
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
    if (!content) {
      return;
    }

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
        if (!response.ok) {
          throw new Error("create failed");
        }
        const body = (await response.json()) as { conversation_id: string };
        activeConversationId = body.conversation_id;
        setConversationId(body.conversation_id);
      }

      setDraft("");
      setMessages((current) => [
        ...current,
        {
          id: `local-${Date.now()}`,
          role: "user",
          content,
          created_at: new Date().toISOString(),
        },
      ]);

      const response = await fetch(
        `${apiUrl}/v1/conversations/${activeConversationId}/messages`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: "user", content, model }),
        },
      );
      if (!response.ok) {
        const body = (await response.json()) as { detail?: string };
        throw new Error(body.detail ?? "send failed");
      }
      const body = (await response.json()) as {
        user_message: Message;
        assistant_message: Message;
      };
      setMessages((current) => [
        ...current.filter((message) => !message.id.startsWith("local-")),
        body.user_message,
        body.assistant_message,
      ]);
      await loadConversations();
    } catch (error) {
      setStatus(
        error instanceof Error ? error.message : "Unable to send message.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">P</div>
          <h1 className="brand-title">Prism</h1>
        </div>
        <button
          className="new-chat"
          disabled={isBusy}
          onClick={createConversation}
        >
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
              <span className="conversation-model">
                {conversation.model_default}
              </span>
              <span className="conversation-meta">
                {conversation.message_count} messages
              </span>
            </button>
          ))}
        </div>
      </aside>

      <section className="chat-pane">
        <header className="toolbar">
          <h2 className="toolbar-title">Chat</h2>
          <select
            className="model-select"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            aria-label="Model"
          >
            {models.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </header>

        <div className="message-list">
          {messages.length === 0 ? (
            <p className="empty-state">
              Start a conversation. Messages are stored by the API and each
              model call is logged through the ingestion pipeline.
            </p>
          ) : (
            messages
              .filter((message) => message.role !== "system")
              .map((message) => (
                <div className={`message ${message.role}`} key={message.id}>
                  {message.content}
                </div>
              ))
          )}
          {status ? <div className="status-line">{status}</div> : null}
        </div>

        <form className="composer" onSubmit={sendMessage}>
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
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
