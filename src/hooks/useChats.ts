import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, API_BASE  } from "../lib/api";

export interface ChatSummary {
  id: string;
  title: string;
  model: string;
  updated_at: string | null;
  pinned?: number;
}

/** Rename and/or pin a chat. */
export async function patchChat(
  id: string,
  patch: { title?: string; pinned?: boolean },
): Promise<void> {
  await apiFetch(`${API_BASE}/chats/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

/** Delete a chat and all its messages. */
export async function deleteChat(id: string): Promise<void> {
  await apiFetch(`${API_BASE}/chats/${id}`, { method: "DELETE" });
}

export interface ChatModel {
  id: string;
  label: string;
  hint: string;
  strength?: number;
  free?: boolean;
  /** True for the "Auto" router entry. Identify Auto by this flag, never by label. */
  auto?: boolean;
  /** True for models served by the local GPU server. */
  local?: boolean;
}

export interface ToolInvocation {
  name: string;
  args?: Record<string, unknown>;
  result?: string;
  status: "running" | "done" | "pending" | "skipped";
  call_id?: string;
  risk?: string;
  cost?: number | null;
}

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
  tools?: ToolInvocation[];
  /** Reasoning/thinking text emitted by reasoning models during streaming. */
  reasoning?: string;
}

export interface UseChatsResult {
  chats: ChatSummary[];
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

export function useChats(): UseChatsResult {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef<boolean>(true);

  const refetch = useCallback(async (): Promise<void> => {
    try {
      const response = await apiFetch(`${API_BASE}/chats`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = (await response.json()) as ChatSummary[];
      if (mounted.current) {
        setChats(data);
        setError(null);
      }
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof Error ? err.message : "Failed to load chats");
      }
    } finally {
      if (mounted.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refetch();
    return () => {
      mounted.current = false;
    };
  }, [refetch]);

  return { chats, loading, error, refetch };
}

export async function fetchChatModels(): Promise<{
  models: ChatModel[];
  default: string;
}> {
  const response = await apiFetch(`${API_BASE}/chat/models`);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return (await response.json()) as { models: ChatModel[]; default: string };
}
