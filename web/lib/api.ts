export type ModelOption = {
  id: string;
  label: string;
  provider: string;
  source: "discovered" | "fallback";
  thinking_supported: boolean;
};

export type Message = {
  id: string;
  role: "user" | "assistant" | "system";
  status: "pending" | "ok" | "error" | "cancelled";
  content: string;
  created_at: string;
  thinking_trace?: string | null;
};

export type ToolCall = {
  id: string;
  name: string;
  status: "running" | "ok" | "error";
  arguments_preview?: string;
  result_preview?: string;
};

export type SseEvent = { event: string; data: Record<string, unknown> };

const apiUrl = (
  process.env.NEXT_PUBLIC_CHATBOT_API_URL || "/api/backend"
).replace(/\/$/, "");

export async function getModels(): Promise<ModelOption[]> {
  const response = await fetch(`${apiUrl}/v1/models`);
  if (!response.ok) {
    throw new Error(await readError(response, "failed to load models"));
  }
  const body = (await safeJson(response)) as { models?: ModelOption[] };
  if (!body.models) {
    throw new Error("failed to load models");
  }
  return body.models;
}

export async function getMessages(conversationId: string): Promise<Message[]> {
  const response = await fetch(
    `${apiUrl}/v1/conversations/${conversationId}/messages`,
  );
  if (!response.ok)
    throw new Error(await readError(response, "failed to load messages"));
  const body = (await safeJson(response)) as { messages?: Message[] };
  if (!body.messages) return [];
  return body.messages;
}

export type Conversation = {
  id: string;
  model_default: string;
  message_count: number;
  title?: string | null;
};

export type ConversationCost = {
  conversation_id: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_prompt_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
};

export type ThinkingEffort = "low" | "medium" | "high" | "xhigh" | "max";

export type ThinkingConfig = {
  enabled: boolean;
  effort?: ThinkingEffort;
};

export async function getConversations(): Promise<Conversation[]> {
  const response = await fetch(`${apiUrl}/v1/conversations`);
  if (!response.ok) {
    throw new Error(await readError(response, "failed to load conversations"));
  }
  const body = (await safeJson(response)) as { conversations?: Conversation[] };
  return body.conversations ?? [];
}

export async function getConversationCost(
  conversationId: string,
): Promise<ConversationCost | null> {
  const response = await fetch(
    `${apiUrl}/v1/conversations/${conversationId}/cost`,
  );
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(await readError(response, "failed to load cost"));
  }
  return (await safeJson(response)) as ConversationCost;
}

export async function patchConversation(
  conversationId: string,
  patch: { title?: string },
): Promise<Conversation> {
  const response = await fetch(`${apiUrl}/v1/conversations/${conversationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!response.ok) {
    throw new Error(await readError(response, "failed to update conversation"));
  }
  return (await safeJson(response)) as Conversation;
}

export async function deleteConversation(
  conversationId: string,
): Promise<void> {
  const response = await fetch(`${apiUrl}/v1/conversations/${conversationId}`, {
    method: "DELETE",
  });
  if (!response.ok && response.status !== 404) {
    throw new Error(await readError(response, "failed to delete conversation"));
  }
}

export async function createConversation(
  modelDefault: string,
): Promise<string> {
  const response = await fetch(`${apiUrl}/v1/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_default: modelDefault }),
  });
  if (!response.ok) {
    throw new Error(await readError(response, "failed to create conversation"));
  }
  const body = (await safeJson(response)) as { conversation_id?: string };
  if (!body.conversation_id) throw new Error("failed to create conversation");
  return body.conversation_id;
}

export async function* readSseStream(
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
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

export type ProviderField = {
  name: string;
  label: string;
  required: boolean;
  default: string | null;
};

export type ProviderSpec = {
  id: string;
  label: string;
  secret_fields: ProviderField[];
  metadata_fields: ProviderField[];
};

export type Credential = {
  id: string;
  provider: string;
  name: string;
  metadata: Record<string, unknown>;
  is_default: boolean;
  last_tested_at: string | null;
  last_test_ok: boolean | null;
  last_test_error: string | null;
};

export async function getProviders(): Promise<ProviderSpec[]> {
  const r = await fetch(`${apiUrl}/v1/providers`);
  if (!r.ok) throw new Error(await readError(r, "failed to load providers"));
  const body = (await safeJson(r)) as { providers?: ProviderSpec[] };
  return body.providers ?? [];
}

export async function getCredentials(): Promise<Credential[]> {
  const r = await fetch(`${apiUrl}/v1/credentials`);
  if (!r.ok) throw new Error(await readError(r, "failed to load credentials"));
  const body = (await safeJson(r)) as { credentials?: Credential[] };
  return body.credentials ?? [];
}

export async function upsertCredential(input: {
  provider: string;
  name: string;
  secrets: Record<string, string>;
  metadata: Record<string, unknown>;
  is_default: boolean;
}): Promise<Credential> {
  const r = await fetch(`${apiUrl}/v1/credentials`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!r.ok) throw new Error(await readError(r, "failed to save credential"));
  return (await safeJson(r)) as Credential;
}

export async function deleteCredential(id: string): Promise<void> {
  const r = await fetch(`${apiUrl}/v1/credentials/${id}`, { method: "DELETE" });
  if (!r.ok && r.status !== 404)
    throw new Error(await readError(r, "failed to delete credential"));
}

export async function validateCredential(input: {
  provider: string;
  secrets: Record<string, string>;
  metadata: Record<string, unknown>;
}): Promise<{ ok: boolean; models: string[]; error: string | null }> {
  const r = await fetch(`${apiUrl}/v1/credentials/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!r.ok) throw new Error(await readError(r, "validation failed"));
  return (await safeJson(r)) as {
    ok: boolean;
    models: string[];
    error: string | null;
  };
}

export { apiUrl };

async function safeJson(response: Response): Promise<unknown> {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(text.slice(0, 240) || "invalid response");
  }
}

async function readError(
  response: Response,
  fallback: string,
): Promise<string> {
  const text = await response.text();
  if (!text) return fallback;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown; error?: unknown };
    if (typeof parsed.error === "string") return parsed.error;
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    return text.slice(0, 240);
  }
  return fallback;
}
