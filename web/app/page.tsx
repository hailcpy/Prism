"use client";

import {
  FormEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Check,
  ChevronDown,
  Send,
  RefreshCw,
  MessageSquarePlus,
  Search,
  Sparkles,
  Square,
  Trash2,
  Wrench,
} from "lucide-react";

import {
  Conversation,
  ConversationCost,
  Message,
  ModelOption,
  ToolCall,
  apiUrl,
  createConversation,
  deleteConversation,
  getConversationCost,
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

type ChatMessage = Message & {
  tool_calls?: ToolCall[];
};

export default function Home() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [model, setModel] = useState<string>(fallbackModel.id);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [modelStatus, setModelStatus] = useState("");
  const [cost, setCost] = useState<ConversationCost | null>(null);
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [thinkingEffort, setThinkingEffort] = useState<
    "low" | "medium" | "high" | "xhigh" | "max"
  >("medium");
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [modelQuery, setModelQuery] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const activeConversationIdRef = useRef<string | null>(null);
  const modelPickerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    activeConversationIdRef.current = conversationId;
  }, [conversationId]);

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
          ? "No live models"
          : `${list.length} model${list.length === 1 ? "" : "s"}`,
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

  const refreshCost = useCallback(async (id: string) => {
    try {
      const c = await getConversationCost(id);
      setCost(c);
    } catch {
      // ignore — cost is best-effort
    }
  }, []);

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      setCost(null);
      return;
    }
    void getMessages(conversationId)
      .then(setMessages)
      .catch((e) => {
        setStatus(e instanceof Error ? e.message : "failed to load messages");
      });
    void refreshCost(conversationId);
  }, [conversationId, refreshCost]);

  useEffect(() => {
    const el = messageListRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  useEffect(() => {
    function onEsc(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (modelMenuOpen) {
        setModelMenuOpen(false);
        return;
      }
      if (pendingDelete) {
        if (!deleteBusy) setPendingDelete(null);
        return;
      }
      abortRef.current?.abort();
    }
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [modelMenuOpen, pendingDelete, deleteBusy]);

  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      if (!modelPickerRef.current?.contains(event.target as Node)) {
        setModelMenuOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, []);

  async function startNewChat() {
    abortRef.current?.abort();
    setConversationId(null);
    setMessages([]);
    setStatus("");
  }

  async function confirmDeleteConversation() {
    if (!pendingDelete) return;
    const id = pendingDelete.id;
    setDeleteBusy(true);
    try {
      await deleteConversation(id);
    } catch (e) {
      setStatus(
        e instanceof Error ? e.message : "failed to delete conversation",
      );
      setDeleteBusy(false);
      return;
    }
    setConversations((current) => current.filter((c) => c.id !== id));
    if (conversationId === id) {
      setConversationId(null);
      setMessages([]);
      setCost(null);
    }
    setDeleteBusy(false);
    setPendingDelete(null);
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const content = draft.trim();
    if (!content) return;
    setBusy(true);
    setStatus("");
    let activeId = conversationId;
    if (!activeId) {
      try {
        const createdId = await createConversation(model);
        activeId = createdId;
        setConversationId(activeId);
        setConversations((current) => [
          {
            id: createdId,
            model_default: model,
            message_count: 0,
          },
          ...current,
        ]);
      } catch (e) {
        setStatus(
          e instanceof Error ? e.message : "failed to create conversation",
        );
        setBusy(false);
        return;
      }
    }
    const streamConversationId = activeId;
    const isActive = () =>
      activeConversationIdRef.current === streamConversationId;
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
    let aborted = false;
    try {
      const response = await fetch(
        `${apiUrl}/v1/conversations/${activeId}/messages`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          },
          body: JSON.stringify({
            role: "user",
            content,
            model,
            thinking:
              selected.thinking_supported && thinkingEnabled
                ? { enabled: true, effort: thinkingEffort }
                : undefined,
          }),
          signal: controller.signal,
        },
      );
      if (!response.ok || !response.body) {
        setStatus((await response.text()) || "request failed");
        return;
      }
      for await (const { event: name, data } of readSseStream(response.body)) {
        if (name === "assistant_message") {
          if (!isActive()) continue;
          const msgId = String(data.id ?? `local-${Date.now()}`);
          const createdAt = String(data.created_at ?? new Date().toISOString());
          setMessages((current) => {
            const last = current[current.length - 1];
            if (last?.role === "assistant" && last.status === "pending") {
              return current;
            }
            return [
              ...current,
              {
                id: msgId,
                role: "assistant",
                status: "pending",
                content: "",
                created_at: createdAt,
              },
            ];
          });
        } else if (name === "token") {
          if (!isActive()) continue;
          const delta = String(data.delta ?? "");
          updatePendingAssistant((message) => ({
            ...message,
            content: message.content + delta,
          }));
        } else if (name === "thinking") {
          if (!isActive()) continue;
          const delta = String(data.delta ?? "");
          if (delta) {
            updatePendingAssistant((message) => ({
              ...message,
              thinking_trace: `${message.thinking_trace ?? ""}${delta}`,
            }));
          }
        } else if (name === "tool_call") {
          if (!isActive()) continue;
          const toolCall = parseToolCall(data);
          if (toolCall) {
            updatePendingAssistant((message) => ({
              ...message,
              tool_calls: mergeToolCall(message.tool_calls ?? [], toolCall),
            }));
          }
        } else if (name === "title") {
          const title = typeof data.title === "string" ? data.title : null;
          if (title) {
            setConversations((current) =>
              current.some((c) => c.id === streamConversationId)
                ? current.map((c) =>
                    c.id === streamConversationId ? { ...c, title } : c,
                  )
                : [
                    {
                      id: streamConversationId,
                      model_default: model,
                      message_count: 0,
                      title,
                    },
                    ...current,
                  ],
            );
          }
        } else if (name === "done") {
          if (isActive()) {
            const loaded = await getMessages(streamConversationId);
            setMessages((current) => mergeLoadedMessages(loaded, current));
          }
        } else if (name === "error") {
          if (!isActive()) continue;
          const detail = data.error as { message?: string } | undefined;
          setStatus(detail?.message ?? "stream error");
        }
      }
      if (isActive()) {
        const loaded = await getMessages(streamConversationId);
        setMessages((current) => mergeLoadedMessages(loaded, current));
      }
      await loadConversations();
      if (isActive()) await refreshCost(streamConversationId);
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        aborted = true;
      } else {
        if (isActive())
          setStatus(e instanceof Error ? e.message : "send failed");
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
      if (aborted && isActive()) {
        await refreshAfterInterruptedStream(streamConversationId);
      }
    }
  }

  function handleKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  function updatePendingAssistant(
    update: (message: ChatMessage) => ChatMessage,
  ) {
    setMessages((current) => {
      const next = [...current];
      const last = next[next.length - 1];
      if (last?.role === "assistant" && last.status === "pending") {
        next[next.length - 1] = update(last);
      } else {
        next.push(
          update({
            id: `local-${Date.now()}`,
            role: "assistant",
            status: "pending",
            content: "",
            created_at: new Date().toISOString(),
          }),
        );
      }
      return next;
    });
  }

  async function refreshAfterInterruptedStream(id: string) {
    await wait(300);
    try {
      const loaded = await getMessages(id);
      setMessages((current) => mergeLoadedMessages(loaded, current));
      await loadConversations();
      await refreshCost(id);
      await wait(900);
      await refreshCost(id);
    } catch (refreshError) {
      setStatus(
        refreshError instanceof Error
          ? refreshError.message
          : "failed to refresh cancelled stream",
      );
    }
  }

  const selected = models.find((m) => m.id === model) ?? fallbackModel;
  const availableModels = models.length > 0 ? models : [fallbackModel];
  const filteredModels = availableModels.filter((item) => {
    const query = modelQuery.trim().toLowerCase();
    if (!query) return true;
    return (
      item.id.toLowerCase().includes(query) ||
      item.label.toLowerCase().includes(query) ||
      item.provider.toLowerCase().includes(query)
    );
  });
  const activeConversation = conversations.find((c) => c.id === conversationId);
  const activeTitle = activeConversation?.title ?? "Chat";
  const providerGroups = Array.from(
    filteredModels.reduce((groups, item) => {
      const list = groups.get(item.provider) ?? [];
      list.push(item);
      groups.set(item.provider, list);
      return groups;
    }, new Map<string, ModelOption[]>()),
  );

  return (
    <div className="flex flex-col md:flex-row h-[calc(100vh-56px)] overflow-hidden bg-mesh-light dark:bg-mesh-dark">
      {/* Sidebar */}
      <aside className="w-full md:w-72 shrink-0 border-r border-black/10 dark:border-white/10 bg-white/40 dark:bg-zinc-900/40 backdrop-blur-3xl flex flex-col p-4 shadow-xl overflow-hidden h-full">
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          className="w-full flex items-center justify-center gap-2 rounded-xl bg-gradient-to-br from-zinc-900 to-zinc-800 dark:from-zinc-100 dark:to-zinc-300 text-white dark:text-zinc-900 px-4 py-3 font-semibold shadow-[0_8px_16px_-6px_rgba(0,0,0,0.3)] dark:shadow-[0_8px_16px_-6px_rgba(255,255,255,0.1)] transition-all mb-6 shrink-0"
          disabled={busy}
          onClick={() => void startNewChat()}
        >
          <MessageSquarePlus className="w-4 h-4" />
          <span>New chat</span>
        </motion.button>

        <div className="flex-1 overflow-y-auto space-y-2 pr-1 custom-scrollbar">
          {conversations.map((c) => (
            <motion.div
              key={c.id}
              whileHover={{ x: 2 }}
              className={`group relative w-full rounded-lg border transition-all ${
                c.id === conversationId
                  ? "border-[#009f8f]/30 bg-gradient-to-br from-[#009f8f]/10 to-transparent dark:border-[#009f8f]/50 dark:from-[#009f8f]/20 shadow-sm"
                  : "border-black/5 dark:border-white/5 bg-white/50 dark:bg-zinc-800/30 hover:bg-white/80 dark:hover:bg-zinc-800/60"
              }`}
            >
              <button
                type="button"
                className="w-full text-left p-3 pr-10"
                onClick={() => {
                  if (c.id !== conversationId) abortRef.current?.abort();
                  setConversationId(c.id);
                  setModel(c.model_default);
                }}
              >
                <span className="block font-semibold text-sm truncate text-zinc-900 dark:text-zinc-100">
                  {c.title ?? c.model_default}
                </span>
                <span className="block text-xs text-zinc-500 dark:text-zinc-400 mt-1 truncate">
                  {c.title ? `${c.model_default} · ` : ""}
                  {c.message_count} messages
                </span>
              </button>
              <div className="pointer-events-none absolute left-2 right-9 top-2 z-20 rounded-md border border-black/10 bg-white px-2.5 py-2 text-xs font-semibold text-zinc-800 opacity-0 shadow-lg transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 dark:border-white/10 dark:bg-zinc-900 dark:text-zinc-100">
                <span className="block break-words">
                  {c.title ?? c.model_default}
                </span>
                {c.title && (
                  <span className="mt-1 block break-words font-normal text-zinc-500 dark:text-zinc-400">
                    {c.model_default}
                  </span>
                )}
              </div>
              <button
                type="button"
                aria-label="Delete conversation"
                onClick={(e) => {
                  e.stopPropagation();
                  setPendingDelete(c);
                }}
                className="absolute top-1/2 -translate-y-1/2 right-2 p-1.5 rounded-md text-zinc-400 hover:text-red-500 hover:bg-red-500/10 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </motion.div>
          ))}
        </div>
      </aside>

      {/* Main Chat Area */}
      <section className="flex-1 flex flex-col min-w-0 bg-transparent h-full overflow-hidden">
        {/* Toolbar */}
        <header className="flex items-center justify-between gap-4 p-4 md:px-8 border-b border-black/5 dark:border-white/5 bg-white/40 dark:bg-zinc-900/40 backdrop-blur-xl shrink-0">
          <div>
            <h2 className="text-lg font-bold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
              <span className="truncate max-w-[48vw]">{activeTitle}</span>
              {selected.thinking_supported && (
                <Sparkles className="w-4 h-4 text-[#2453ff] dark:text-[#ff6d4d]" />
              )}
            </h2>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 capitalize flex items-center gap-1.5 mt-0.5">
              <span>{selected.provider}</span>
              {selected.thinking_supported && <span>· thinking available</span>}
              {selected.source === "fallback" && <span>· fallback</span>}
            </p>
          </div>

          <div className="flex items-center gap-3">
            {cost && cost.calls > 0 && (
              <span
                className="text-xs font-semibold px-2.5 py-1 rounded-full border border-[#009f8f]/30 bg-[#009f8f]/10 text-zinc-700 dark:text-zinc-200"
                aria-label={`prompt ${cost.prompt_tokens.toLocaleString()}, completion ${cost.completion_tokens.toLocaleString()}, cached ${cost.cached_prompt_tokens.toLocaleString()}, reasoning ${cost.reasoning_tokens.toLocaleString()}, ${cost.calls} call${cost.calls === 1 ? "" : "s"}`}
              >
                {formatCostShort(cost.cost_usd)} ·{" "}
                {formatTokens(
                  cost.prompt_tokens +
                    cost.completion_tokens +
                    cost.reasoning_tokens,
                )}{" "}
                tok
              </span>
            )}
            {modelStatus && (
              <span className="text-xs text-zinc-500 hidden sm:inline-block">
                {modelStatus}
              </span>
            )}
            <button
              type="button"
              className="p-2 rounded-lg border border-black/10 dark:border-white/10 bg-white/50 dark:bg-zinc-800/50 hover:bg-white dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300 transition-colors"
              onClick={() => void loadModels()}
              aria-label="Refresh models"
            >
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
        </header>

        {/* Message List */}
        <div
          className="flex-1 overflow-y-auto p-4 md:p-8 space-y-6"
          ref={messageListRef}
          tabIndex={0}
        >
          {messages.length === 0 ? (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="max-w-lg mx-auto mt-20 border border-black/5 dark:border-white/5 rounded-2xl bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md p-8 text-center shadow-lg"
            >
              <div className="w-16 h-1.5 mx-auto mb-6 rounded-full bg-gradient-to-r from-[#ff6d4d] via-[#009f8f] to-[#2453ff] shadow-[0_0_20px_rgba(0,159,143,0.3)]" />
              <p className="text-zinc-600 dark:text-zinc-300 leading-relaxed">
                Start a conversation. Prism stores the chat, streams model
                output, and keeps thinking traces when the provider emits them.
              </p>
            </motion.div>
          ) : (
            <AnimatePresence initial={false}>
              {messages
                .filter((m) => m.role !== "system")
                .map((m) => (
                  <motion.div
                    initial={{ opacity: 0, y: 10, scale: 0.98 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    key={m.id}
                    className={`flex flex-col max-w-[85%] ${m.role === "user" ? "ml-auto items-end" : "mr-auto items-start"}`}
                  >
                    <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-500 mb-1.5 pl-1">
                      {m.role}
                    </div>

                    {m.role === "assistant" &&
                      (m.thinking_trace ||
                        (m.status === "pending" && !m.content)) && (
                        <ThinkingTracePanel
                          trace={m.thinking_trace ?? ""}
                          pending={m.status === "pending" && !m.content}
                        />
                      )}

                    {m.tool_calls && m.tool_calls.length > 0 && (
                      <div className="mb-2 w-full max-w-full space-y-1.5">
                        {m.tool_calls.map((toolCall) => (
                          <ToolCallPanel
                            key={toolCall.id}
                            toolCall={toolCall}
                          />
                        ))}
                      </div>
                    )}

                    <div
                      className={`px-5 py-3.5 rounded-2xl shadow-sm text-[15px] leading-relaxed whitespace-pre-wrap break-words ${
                        m.role === "user"
                          ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900 rounded-tr-sm"
                          : "bg-white dark:bg-zinc-800 text-zinc-800 dark:text-zinc-200 border border-black/5 dark:border-white/5 rounded-tl-sm shadow-[0_4px_12px_rgba(0,0,0,0.02)]"
                      }`}
                    >
                      {m.role === "assistant" &&
                      m.status === "pending" &&
                      !m.content ? (
                        <TypingDots />
                      ) : (
                        m.content
                      )}
                    </div>
                  </motion.div>
                ))}
            </AnimatePresence>
          )}
          {status && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-center text-sm font-medium text-[#ff6d4d]"
            >
              {status}
            </motion.div>
          )}
        </div>

        {/* Composer */}
        <div className="p-4 md:p-6 bg-transparent shrink-0">
          <form
            className="max-w-4xl mx-auto flex flex-col gap-2 rounded-2xl border border-black/10 dark:border-white/10 bg-white/70 dark:bg-zinc-900/70 p-3 shadow-[0_8px_32px_rgba(0,0,0,0.04)] backdrop-blur-xl focus-within:border-[#009f8f]/50 focus-within:ring-2 focus-within:ring-[#009f8f]/10 transition-all"
            onSubmit={send}
          >
            <div className="flex items-center gap-2 px-1 mb-1 relative border-b border-black/5 dark:border-white/5 pb-2">
              <div className="relative min-w-0 flex-1" ref={modelPickerRef}>
                <button
                  type="button"
                  className="flex max-w-full items-center gap-1.5 rounded px-2 py-0.5 text-left text-xs font-semibold text-zinc-700 outline-none transition-colors hover:bg-black/5 focus-visible:ring-2 focus-visible:ring-[#009f8f]/30 dark:text-zinc-300 dark:hover:bg-white/5"
                  aria-haspopup="listbox"
                  aria-expanded={modelMenuOpen}
                  onClick={() => {
                    setModelMenuOpen((open) => !open);
                    setModelQuery("");
                  }}
                >
                  <Sparkles className="h-3.5 w-3.5 shrink-0 text-zinc-400" />
                  <span className="truncate">{selected.label}</span>
                  <ChevronDown className="h-3.5 w-3.5 shrink-0 text-zinc-400" />
                </button>

                {modelMenuOpen && (
                  <div className="absolute bottom-full left-0 z-40 mb-2 w-[min(24rem,calc(100vw-3rem))] overflow-hidden rounded-lg border border-black/10 bg-white shadow-2xl dark:border-white/10 dark:bg-zinc-900">
                    <div className="flex items-center gap-2 border-b border-black/5 px-3 py-2 dark:border-white/10">
                      <Search className="h-3.5 w-3.5 shrink-0 text-zinc-400" />
                      <input
                        value={modelQuery}
                        onChange={(e) => setModelQuery(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") e.preventDefault();
                        }}
                        className="min-w-0 flex-1 bg-transparent text-xs font-medium text-zinc-800 outline-none placeholder:text-zinc-400 dark:text-zinc-100"
                        placeholder="Filter models"
                        autoFocus
                      />
                    </div>
                    <div
                      role="listbox"
                      aria-label="Model"
                      className="max-h-72 overflow-y-auto p-1.5"
                    >
                      {providerGroups.length === 0 ? (
                        <div className="px-2 py-6 text-center text-xs text-zinc-500 dark:text-zinc-400">
                          No models match
                        </div>
                      ) : (
                        providerGroups.map(([provider, items]) => (
                          <div key={provider} className="py-1">
                            <div className="px-2 pb-1 text-[10px] font-bold uppercase text-zinc-400">
                              {provider}
                            </div>
                            {items.map((item) => (
                              <button
                                key={item.id}
                                type="button"
                                role="option"
                                aria-selected={item.id === model}
                                className="flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-2 text-left text-xs text-zinc-700 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800"
                                onClick={() => {
                                  setModel(item.id);
                                  setModelMenuOpen(false);
                                  setModelQuery("");
                                }}
                              >
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate font-semibold">
                                    {item.label}
                                  </span>
                                  <span className="block truncate text-[11px] text-zinc-500 dark:text-zinc-400">
                                    {item.id}
                                    {item.source === "fallback"
                                      ? " · fallback"
                                      : ""}
                                  </span>
                                </span>
                                {item.id === model && (
                                  <Check className="h-3.5 w-3.5 shrink-0 text-[#009f8f]" />
                                )}
                              </button>
                            ))}
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                )}
              </div>
              {selected.thinking_supported && (
                <div className="ml-auto flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-300">
                  <label className="flex items-center gap-1.5 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={thinkingEnabled}
                      onChange={(e) => setThinkingEnabled(e.target.checked)}
                      className="accent-[#2453ff]"
                    />
                    <span className="font-semibold">Thinking</span>
                  </label>
                  {thinkingEnabled && (
                    <div
                      role="radiogroup"
                      aria-label="Thinking effort"
                      className="flex items-center gap-0.5 rounded-md border border-zinc-300 dark:border-zinc-700 p-0.5"
                    >
                      {(["low", "medium", "high", "xhigh", "max"] as const).map(
                        (level) => (
                          <button
                            key={level}
                            type="button"
                            role="radio"
                            aria-checked={thinkingEffort === level}
                            onClick={() => setThinkingEffort(level)}
                            className={`px-1.5 py-0.5 rounded text-[11px] font-medium uppercase tracking-wide transition-colors ${
                              thinkingEffort === level
                                ? "bg-[#2453ff] text-white"
                                : "text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                            }`}
                          >
                            {level}
                          </button>
                        ),
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="flex items-end gap-3 w-full">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={handleKey}
                placeholder="Message Prism..."
                className="flex-1 max-h-48 min-h-[44px] bg-transparent resize-none outline-none px-2 py-2.5 text-zinc-900 dark:text-zinc-100 placeholder:text-zinc-400 dark:placeholder:text-zinc-500 custom-scrollbar text-[15px]"
                aria-label="Message"
              />
              {busy ? (
                <motion.button
                  type="button"
                  whileHover={{ scale: 1.05 }}
                  whileTap={{ scale: 0.95 }}
                  onClick={(e) => {
                    e.preventDefault();
                    abortRef.current?.abort();
                  }}
                  className="p-3 mb-0.5 mr-0.5 rounded-xl flex items-center justify-center transition-all bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900 shadow-lg"
                  aria-label="Stop generating"
                >
                  <Square className="w-5 h-5 fill-current" />
                </motion.button>
              ) : (
                <motion.button
                  type="submit"
                  whileHover={{ scale: draft.trim() ? 1.05 : 1 }}
                  whileTap={{ scale: draft.trim() ? 0.95 : 1 }}
                  className={`p-3 mb-0.5 mr-0.5 rounded-xl flex items-center justify-center transition-all ${
                    !draft.trim()
                      ? "bg-zinc-200 dark:bg-zinc-800 text-zinc-400 dark:text-zinc-500 cursor-not-allowed"
                      : "bg-gradient-to-br from-[#ff6d4d] to-[#2453ff] text-white shadow-lg shadow-[#ff6d4d]/20 hover:shadow-[#2453ff]/30"
                  }`}
                  disabled={!draft.trim()}
                >
                  <Send className="w-5 h-5" />
                </motion.button>
              )}
            </div>
          </form>
          <div className="text-center mt-3 text-[11px] text-zinc-400 dark:text-zinc-500 font-medium tracking-wide">
            Prism securely processes your data locally and via configured
            providers.
          </div>
        </div>
      </section>
      <AnimatePresence>
        {pendingDelete && (
          <motion.div
            key="delete-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
            onClick={() => !deleteBusy && setPendingDelete(null)}
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-conversation-title"
          >
            <motion.div
              key="delete-card"
              initial={{ opacity: 0, scale: 0.96, y: 8 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 8 }}
              transition={{ duration: 0.15 }}
              onClick={(e) => e.stopPropagation()}
              className="w-full max-w-sm rounded-2xl bg-white dark:bg-zinc-900 border border-black/10 dark:border-white/10 shadow-2xl p-6"
            >
              <div className="flex items-start gap-3">
                <div className="shrink-0 w-10 h-10 rounded-full bg-red-500/10 text-red-500 flex items-center justify-center">
                  <Trash2 className="w-5 h-5" />
                </div>
                <div className="flex-1 min-w-0">
                  <h2
                    id="delete-conversation-title"
                    className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
                  >
                    Delete conversation?
                  </h2>
                  <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
                    “{pendingDelete.title ?? pendingDelete.model_default}” and
                    all its messages will be permanently removed.
                  </p>
                </div>
              </div>
              <div className="mt-6 flex justify-end gap-2">
                <button
                  type="button"
                  disabled={deleteBusy}
                  onClick={() => setPendingDelete(null)}
                  className="px-4 py-2 rounded-lg text-sm font-semibold text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={deleteBusy}
                  onClick={() => void confirmDeleteConversation()}
                  className="px-4 py-2 rounded-lg text-sm font-semibold bg-red-600 text-white hover:bg-red-700 disabled:opacity-60 disabled:cursor-not-allowed shadow-sm"
                >
                  {deleteBusy ? "Deleting…" : "Delete"}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1 h-5">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-zinc-400 dark:bg-zinc-500"
          animate={{ opacity: [0.3, 1, 0.3], y: [0, -2, 0] }}
          transition={{ duration: 1, repeat: Infinity, delay: i * 0.15 }}
        />
      ))}
    </span>
  );
}

function ThinkingTracePanel({
  trace,
  pending,
}: {
  trace: string;
  pending: boolean;
}) {
  return (
    <div className="mb-2 w-full max-w-full border-l-2 border-zinc-300 dark:border-zinc-700 pl-3 py-1 text-zinc-500 dark:text-zinc-400">
      <div className="text-[11px] font-semibold uppercase text-zinc-400 dark:text-zinc-500 mb-1">
        Thinking
      </div>
      {trace ? (
        <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">
          {trace}
        </div>
      ) : pending ? (
        <div className="text-sm italic">Thinking...</div>
      ) : null}
    </div>
  );
}

function ToolCallPanel({ toolCall }: { toolCall: ToolCall }) {
  const statusLabel =
    toolCall.status === "running"
      ? "Running"
      : toolCall.status === "error"
        ? "Error"
        : "Done";
  return (
    <div className="w-full max-w-full rounded-xl border border-[#009f8f]/15 dark:border-[#009f8f]/25 bg-[#009f8f]/5 dark:bg-[#009f8f]/10 px-4 py-2.5 text-sm text-zinc-800 dark:text-zinc-200">
      <div className="flex items-center gap-2 min-w-0">
        <Wrench className="w-3.5 h-3.5 shrink-0 text-[#009f8f]" />
        <span className="font-semibold truncate">{toolCall.name}</span>
        <span
          className={`ml-auto shrink-0 text-[11px] font-semibold uppercase ${
            toolCall.status === "error"
              ? "text-[#ff6d4d]"
              : "text-zinc-500 dark:text-zinc-400"
          }`}
        >
          {statusLabel}
        </span>
      </div>
      {toolCall.arguments_preview && (
        <div className="mt-2 font-mono text-xs whitespace-pre-wrap break-words text-zinc-600 dark:text-zinc-300">
          {toolCall.arguments_preview}
        </div>
      )}
      {toolCall.result_preview && (
        <div className="mt-2 border-t border-black/5 dark:border-white/10 pt-2 text-xs whitespace-pre-wrap break-words text-zinc-600 dark:text-zinc-300">
          {toolCall.result_preview}
        </div>
      )}
    </div>
  );
}

function parseToolCall(data: Record<string, unknown>): ToolCall | null {
  const name = typeof data.name === "string" ? data.name : "unknown";
  const id =
    typeof data.id === "string" && data.id ? data.id : `${name}-${Date.now()}`;
  const rawStatus = typeof data.status === "string" ? data.status : "running";
  const status =
    rawStatus === "ok" || rawStatus === "error" ? rawStatus : "running";
  return {
    id,
    name,
    status,
    arguments_preview:
      typeof data.arguments_preview === "string"
        ? data.arguments_preview
        : undefined,
    result_preview:
      typeof data.result_preview === "string" ? data.result_preview : undefined,
  };
}

function mergeToolCall(toolCalls: ToolCall[], next: ToolCall): ToolCall[] {
  const existingIndex = toolCalls.findIndex((item) => item.id === next.id);
  if (existingIndex === -1) return [...toolCalls, next];
  return toolCalls.map((item, index) =>
    index === existingIndex
      ? {
          ...item,
          ...next,
          name: next.name === "unknown" ? item.name : next.name,
          arguments_preview: next.arguments_preview ?? item.arguments_preview,
          result_preview: next.result_preview ?? item.result_preview,
        }
      : item,
  );
}

function mergeLoadedMessages(
  loaded: Message[],
  current: ChatMessage[],
): ChatMessage[] {
  const currentById = new Map(current.map((message) => [message.id, message]));
  return loaded.map((message) => {
    const existing = currentById.get(message.id);
    return {
      ...message,
      thinking_trace: message.thinking_trace ?? existing?.thinking_trace,
      tool_calls: existing?.tool_calls,
    };
  });
}

async function wait(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

function formatCostShort(v: number): string {
  if (!Number.isFinite(v) || v === 0) return "$0";
  if (v >= 1) return `$${v.toFixed(2)}`;
  if (v >= 0.01) return `$${v.toFixed(3)}`;
  return `$${v.toFixed(4)}`;
}

function formatTokens(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return v.toString();
}
