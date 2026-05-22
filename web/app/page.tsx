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
import { Send, RefreshCw, MessageSquarePlus, Sparkles, Square } from "lucide-react";

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
    if (busy) return;
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
            <motion.button
              key={c.id}
              whileHover={{ x: 2 }}
              className={`w-full text-left p-3 rounded-lg border transition-all ${
                c.id === conversationId
                  ? "border-[#009f8f]/30 bg-gradient-to-br from-[#009f8f]/10 to-transparent dark:border-[#009f8f]/50 dark:from-[#009f8f]/20 shadow-sm"
                  : "border-black/5 dark:border-white/5 bg-white/50 dark:bg-zinc-800/30 hover:bg-white/80 dark:hover:bg-zinc-800/60"
              }`}
              onClick={() => {
                setConversationId(c.id);
                setModel(c.model_default);
              }}
            >
              <span className="block font-semibold text-sm truncate text-zinc-900 dark:text-zinc-100">
                {c.model_default}
              </span>
              <span className="block text-xs text-zinc-500 dark:text-zinc-400 mt-1">
                {c.message_count} messages
              </span>
            </motion.button>
          ))}
        </div>
      </aside>

      {/* Main Chat Area */}
      <section className="flex-1 flex flex-col min-w-0 bg-transparent h-full overflow-hidden">
        {/* Toolbar */}
        <header className="flex items-center justify-between gap-4 p-4 md:px-8 border-b border-black/5 dark:border-white/5 bg-white/40 dark:bg-zinc-900/40 backdrop-blur-xl shrink-0">
          <div>
            <h2 className="text-lg font-bold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
              Chat
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
            {modelStatus && (
              <span className="text-xs text-zinc-500 hidden sm:inline-block">
                {modelStatus}
              </span>
            )}
            <button
              type="button"
              className="p-2 rounded-lg border border-black/10 dark:border-white/10 bg-white/50 dark:bg-zinc-800/50 hover:bg-white dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300 transition-colors"
              onClick={() => void loadModels()}
              title="Refresh models"
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
                    className={`flex flex-col max-w-[85%] ${m.role === 'user' ? 'ml-auto items-end' : 'mr-auto items-start'}`}
                  >
                    <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-500 mb-1.5 pl-1">
                      {m.role}
                    </div>
                    
                    {m.thinking_trace && (
                      <details className="mb-2 w-full max-w-full border border-[#2453ff]/10 dark:border-[#2453ff]/20 rounded-xl bg-[#2453ff]/5 dark:bg-[#2453ff]/10 text-zinc-800 dark:text-zinc-200 text-sm overflow-hidden group">
                        <summary className="cursor-pointer px-4 py-2.5 font-semibold hover:bg-black/5 dark:hover:bg-white/5 transition-colors select-none">
                          Thinking trace
                        </summary>
                        <div className="px-4 py-3 border-t border-[#2453ff]/5 dark:border-[#2453ff]/10 whitespace-pre-wrap font-mono text-xs opacity-90 leading-relaxed">
                          {m.thinking_trace}
                        </div>
                      </details>
                    )}
                    
                    <div className={`px-5 py-3.5 rounded-2xl shadow-sm text-[15px] leading-relaxed whitespace-pre-wrap break-words ${
                      m.role === 'user' 
                        ? 'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900 rounded-tr-sm' 
                        : 'bg-white dark:bg-zinc-800 text-zinc-800 dark:text-zinc-200 border border-black/5 dark:border-white/5 rounded-tl-sm shadow-[0_4px_12px_rgba(0,0,0,0.02)]'
                    }`}>
                      {m.content}
                    </div>
                  </motion.div>
                ))}
            </AnimatePresence>
          )}
          {status && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-center text-sm font-medium text-[#ff6d4d]">
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
              <Sparkles className="w-3.5 h-3.5 text-zinc-400" />
              <select
                className="bg-transparent appearance-none hover:bg-black/5 dark:hover:bg-white/5 px-2 py-0.5 rounded text-xs font-semibold text-zinc-700 dark:text-zinc-300 outline-none cursor-pointer max-w-full transition-colors"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                aria-label="Model"
                style={{ WebkitAppearance: 'none', MozAppearance: 'none' }}
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
                      ? 'bg-zinc-200 dark:bg-zinc-800 text-zinc-400 dark:text-zinc-500 cursor-not-allowed'
                      : 'bg-gradient-to-br from-[#ff6d4d] to-[#2453ff] text-white shadow-lg shadow-[#ff6d4d]/20 hover:shadow-[#2453ff]/30'
                  }`}
                  disabled={!draft.trim()}
                >
                  <Send className="w-5 h-5" />
                </motion.button>
              )}
            </div>
          </form>
          <div className="text-center mt-3 text-[11px] text-zinc-400 dark:text-zinc-500 font-medium tracking-wide">
            Prism securely processes your data locally and via configured providers.
          </div>
        </div>
      </section>
    </div>
  );
}
