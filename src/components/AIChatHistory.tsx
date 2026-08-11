import React, { useState, useEffect, useCallback } from "react";
import {
  Search,
  RefreshCw,
  MessageSquare,
  Clock,
  Folder,
  Bot,
  Terminal,
  Sparkles,
  Filter,
  X,
  ChevronLeft,
  Database,
} from "lucide-react";
import { apiFetch, API_BASE } from "../lib/api";

/**
 * AIChatHistory - unified read-only view of synced AI chat histories.
 *
 * Sources: claude / codex / gpt / qoder / opencode (whatever the backend
 * has synced into ai_chats.db). All requests go through apiFetch so the
 * session bearer token is attached (raw fetch() would 401).
 */

interface AIConversation {
  id: number;
  source: string;
  title: string;
  project_path: string | null;
  started_at: string;
  message_count: number;
}

interface AIChatMessage {
  role: string;
  content: string;
  timestamp: string | null;
}

const SOURCE_ICONS: Record<string, React.ReactNode> = {
  claude: <Bot className="w-4 h-4 text-purple-400" />,
  codex: <Terminal className="w-4 h-4 text-green-400" />,
  gpt: <Sparkles className="w-4 h-4 text-blue-400" />,
  qoder: <Database className="w-4 h-4 text-sky-400" />,
  opencode: <Terminal className="w-4 h-4 text-orange-400" />,
  copilot: <Bot className="w-4 h-4 text-cyan-400" />,
};

const SOURCE_COLORS: Record<string, string> = {
  claude: "bg-purple-900/50 text-purple-300 border-purple-700",
  codex: "bg-green-900/50 text-green-300 border-green-700",
  gpt: "bg-blue-900/50 text-blue-300 border-blue-700",
  qoder: "bg-sky-900/50 text-sky-300 border-sky-700",
  opencode: "bg-orange-900/50 text-orange-300 border-orange-700",
  copilot: "bg-cyan-900/50 text-cyan-300 border-cyan-700",
};

export function AIChatHistory({ onClose }: { onClose: () => void }) {
  const [conversations, setConversations] = useState<AIConversation[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [sources, setSources] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<AIConversation | null>(null);
  const [messages, setMessages] = useState<AIChatMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);

  const fetchStats = useCallback(async () => {
    try {
      const res = await apiFetch(`${API_BASE}/ai-chats/stats`);
      const data = await res.json();
      if (!data.error && data.stats) {
        setStats(data.stats as Record<string, number>);
        setSources(Object.keys(data.stats as Record<string, number>).sort());
      }
    } catch {
      /* stats are best-effort */
    }
  }, []);

  const fetchConversations = useCallback(async (source?: string, query?: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (source && source !== "all") params.set("source", source);
      if (query && query.trim()) params.set("q", query.trim());
      const endpoint = query && query.trim() ? "ai-chats/search" : "ai-chats";
      const res = await apiFetch(`${API_BASE}/${endpoint}?${params.toString()}`);
      const data = await res.json();
      if (data.error) {
        setError(String(data.error));
      } else {
        setConversations((query && query.trim() ? data.results : data.conversations) || []);
      }
    } catch (err) {
      setError("Failed to load conversations");
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const syncAll = async () => {
    setIsSyncing(true);
    setError(null);
    try {
      const res = await apiFetch(`${API_BASE}/ai-chats/sync`, { method: "POST" });
      const data = await res.json();
      if (data.error) {
        setError(String(data.error));
      } else {
        await fetchStats();
        await fetchConversations(activeTab === "all" ? undefined : activeTab, searchQuery);
      }
    } catch {
      setError("Sync failed");
    } finally {
      setIsSyncing(false);
    }
  };

  const openConversation = async (conv: AIConversation) => {
    setSelected(conv);
    setMessagesLoading(true);
    setMessages([]);
    try {
      const res = await apiFetch(`${API_BASE}/ai-chats/${conv.id}/messages`);
      const data = await res.json();
      setMessages(data.messages || []);
    } catch {
      setMessages([]);
    } finally {
      setMessagesLoading(false);
    }
  };

  useEffect(() => {
    fetchStats();
    fetchConversations();
  }, [fetchStats, fetchConversations]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const formatDate = (s: string | null) => {
    if (!s) return "Unknown";
    const d = new Date(s);
    return (
      d.toLocaleDateString() +
      " " +
      d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    );
  };

  const projectName = (p: string | null) => {
    if (!p) return "No project";
    const parts = p.split(/[\\/]/);
    return parts[parts.length - 1] || p;
  };

  const total = Object.values(stats).reduce((a, b) => a + b, 0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="flex flex-col w-[900px] max-w-[94vw] h-[82vh] bg-gray-900 border border-gray-700 rounded-xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <div className="flex items-center gap-2">
            {selected ? (
              <button
                onClick={() => {
                  setSelected(null);
                  setMessages([]);
                }}
                className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200"
              >
                <ChevronLeft className="w-4 h-4" /> Back
              </button>
            ) : (
              <MessageSquare className="w-5 h-5 text-purple-400" />
            )}
            <h2 className="text-lg font-semibold">
              {selected ? selected.title || "Untitled" : "AI Chat History"}
            </h2>
            {!selected && (
              <span className="text-xs text-gray-500">
                (claude • codex • gpt • qoder • opencode)
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={syncAll}
              disabled={isSyncing}
              className="flex items-center gap-1 px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-md transition-colors"
            >
              <RefreshCw className={`w-4 h-4 ${isSyncing ? "animate-spin" : ""}`} />
              {isSyncing ? "Syncing..." : "Sync All"}
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded-md text-gray-400 hover:text-gray-200 hover:bg-gray-800"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Stats bar */}
        <div className="flex items-center gap-4 px-4 py-2 bg-gray-800/50 border-b border-gray-700 text-xs">
          {sources.map((src) => (
            <span key={src} className="flex items-center gap-1">
              {SOURCE_ICONS[src] || <MessageSquare className="w-3 h-3" />}
              <span className="capitalize">{src}:</span>
              <span className="font-medium">{stats[src]}</span>
            </span>
          ))}
          <div className="flex-1" />
          <span className="text-gray-500">Total: {total} conversations</span>
        </div>

        {selected ? (
          /* Message detail view */
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {messagesLoading ? (
              <div className="flex items-center justify-center h-32 text-gray-500">
                <RefreshCw className="w-5 h-5 animate-spin mr-2" /> Loading messages...
              </div>
            ) : messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-32 text-gray-500">
                <MessageSquare className="w-8 h-8 mb-2 opacity-50" />
                <p>No text messages in this conversation</p>
              </div>
            ) : (
              messages.map((m, i) => (
                <div key={i} className="flex gap-3">
                  <span
                    className={`flex-none w-16 text-right text-[11px] uppercase mt-1 ${
                      m.role === "user" ? "text-sky-400" : "text-purple-400"
                    }`}
                  >
                    {m.role}
                  </span>
                  <div className="flex-1 bg-gray-800 border border-gray-700 rounded-md p-3 text-sm whitespace-pre-wrap break-words">
                    {m.content || <span className="text-gray-600">(empty)</span>}
                  </div>
                </div>
              ))
            )}
          </div>
        ) : (
          <>
            {/* Tabs */}
            <div className="flex border-b border-gray-700 overflow-x-auto">
              {(["all", ...sources] as string[]).map((tab) => (
                <button
                  key={tab}
                  onClick={() => {
                    setActiveTab(tab);
                    fetchConversations(tab === "all" ? undefined : tab, searchQuery);
                  }}
                  className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap
                    ${activeTab === tab
                      ? "border-blue-500 text-blue-400"
                      : "border-transparent text-gray-400 hover:text-gray-200"
                    }`}
                >
                  <span className="flex items-center gap-1">
                    {tab === "all" && <Filter className="w-3 h-3" />}
                    {SOURCE_ICONS[tab]}
                    <span className="capitalize">{tab}</span>
                    {tab !== "all" && stats[tab] > 0 && (
                      <span className="ml-1 text-xs bg-gray-700 px-1 rounded">{stats[tab]}</span>
                    )}
                  </span>
                </button>
              ))}
            </div>

            {/* Search */}
            <div className="p-4 border-b border-gray-700">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search across all AI conversations..."
                  value={searchQuery}
                  onChange={(e) => {
                    setSearchQuery(e.target.value);
                    fetchConversations(activeTab === "all" ? undefined : activeTab, e.target.value);
                  }}
                  className="w-full pl-10 pr-4 py-2 bg-gray-800 border border-gray-600 rounded-md text-sm focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>

            {error && (
              <div className="mx-4 mt-3 p-3 bg-red-900/50 border border-red-700 rounded-md text-sm text-red-200">
                {error}
              </div>
            )}

            {/* List */}
            <div className="flex-1 overflow-y-auto p-4">
              {isLoading ? (
                <div className="flex items-center justify-center h-32 text-gray-500">
                  <RefreshCw className="w-5 h-5 animate-spin mr-2" /> Loading...
                </div>
              ) : conversations.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-32 text-gray-500">
                  <MessageSquare className="w-8 h-8 mb-2 opacity-50" />
                  <p>No conversations found</p>
                  <button onClick={syncAll} className="mt-2 text-sm text-blue-400 hover:underline">
                    Sync now to import from AI tools
                  </button>
                </div>
              ) : (
                <div className="space-y-2">
                  {conversations.map((conv) => (
                    <button
                      key={conv.id}
                      onClick={() => openConversation(conv)}
                      className="w-full text-left p-3 rounded-md cursor-pointer transition-colors border bg-gray-800 border-gray-700 hover:border-gray-600"
                    >
                      <div className="flex items-start gap-2">
                        <div className="mt-0.5">
                          {SOURCE_ICONS[conv.source] || (
                            <MessageSquare className="w-4 h-4 text-gray-400" />
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <h3 className="text-sm font-medium text-gray-200 line-clamp-2">
                            {conv.title || "Untitled conversation"}
                          </h3>
                          <div className="flex items-center gap-3 mt-1.5 text-xs text-gray-500">
                            <span
                              className={`px-1.5 py-0.5 rounded border text-xs capitalize ${
                                SOURCE_COLORS[conv.source] ||
                                "bg-gray-700 text-gray-300 border-gray-600"
                              }`}
                            >
                              {conv.source}
                            </span>
                            <span className="flex items-center gap-1">
                              <Clock className="w-3 h-3" />
                              {formatDate(conv.started_at)}
                            </span>
                            {conv.project_path && (
                              <span className="flex items-center gap-1 truncate max-w-[150px]">
                                <Folder className="w-3 h-3" />
                                {projectName(conv.project_path)}
                              </span>
                            )}
                            <span className="text-gray-600">{conv.message_count} messages</span>
                          </div>
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        {/* Footer */}
        <div className="p-3 border-t border-gray-700 text-xs text-gray-500">
          Read-only view of synced AI conversations (ai_chats.db). Original tool histories stay
          untouched.
        </div>
      </div>
    </div>
  );
}

export default AIChatHistory;
