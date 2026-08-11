import { apiFetch } from "../lib/api";
/**
 * AIChatHistory - Unified view of Claude, Codex, and GPT chat histories
 * 
 * Displays read-only copies of conversations from multiple AI assistants,
 * synced from their original locations to Infinity's database.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { 
  Search, 
  RefreshCw, 
  MessageSquare, 
  Clock, 
  Folder, 
  Bot,
  Terminal,
  Sparkles,
  Filter
} from 'lucide-react';
import { API_BASE } from '../lib/api';

interface AIConversation {
  id: number;
  source_id: string;
  source: 'claude' | 'codex' | 'gpt' | 'copilot' | string;
  title: string;
  project_path: string | null;
  started_at: string;
  updated_at: string | null;
  message_count: number;
  imported_at: string;
}

interface SourceStats {
  [key: string]: number;
}

type TabType = 'all' | 'claude' | 'codex' | 'gpt';

const SOURCE_ICONS: Record<string, React.ReactNode> = {
  claude: <Bot className="w-4 h-4 text-purple-400" />,
  codex: <Terminal className="w-4 h-4 text-green-400" />,
  gpt: <Sparkles className="w-4 h-4 text-blue-400" />,
  copilot: <Bot className="w-4 h-4 text-cyan-400" />,
};

const SOURCE_COLORS: Record<string, string> = {
  claude: 'bg-purple-900/50 text-purple-300 border-purple-700',
  codex: 'bg-green-900/50 text-green-300 border-green-700',
  gpt: 'bg-blue-900/50 text-blue-300 border-blue-700',
  copilot: 'bg-cyan-900/50 text-cyan-300 border-cyan-700',
};

export function AIChatHistory() {
  const [conversations, setConversations] = useState<AIConversation[]>([]);
  const [stats, setStats] = useState<SourceStats>({});
  const [activeTab, setActiveTab] = useState<TabType>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedConv, setSelectedConv] = useState<AIConversation | null>(null);

  // Fetch conversations
  const fetchConversations = useCallback(async (source?: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const url = source && source !== 'all' 
        ? `${API_BASE}/ai-chats?source=${source}&limit=100`
        : `${API_BASE}/ai-chats?limit=100`;
      
      const response = await fetch(url);
      const data = await response.json();
      
      if (data.error) {
        setError(data.error);
      } else {
        setConversations(data.conversations || []);
      }
    } catch (err) {
      setError('Failed to load conversations');
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Fetch stats
  const fetchStats = useCallback(async () => {
    try {
      const response = await apiFetch(`${API_BASE}/ai-chats/stats`);
      const data = await response.json();
      if (!data.error) {
        setStats(data.stats || {});
      }
    } catch (err) {
      console.error('Failed to fetch stats:', err);
    }
  }, []);

  // Sync all sources
  const syncAll = async () => {
    setIsSyncing(true);
    setError(null);
    try {
      const response = await apiFetch(`${API_BASE}/ai-chats/sync`, {
        method: 'POST',
      });
      const data = await response.json();
      
      if (data.error) {
        setError(data.error);
      } else {
        await fetchConversations(activeTab === 'all' ? undefined : activeTab);
        await fetchStats();
      }
    } catch (err) {
      setError('Sync failed');
      console.error(err);
    } finally {
      setIsSyncing(false);
    }
  };

  // Search conversations
  const handleSearch = async (query: string) => {
    setSearchQuery(query);
    if (!query.trim()) {
      fetchConversations(activeTab === 'all' ? undefined : activeTab);
      return;
    }
    
    try {
      const url = activeTab !== 'all'
        ? `${API_BASE}/ai-chats/search?q=${encodeURIComponent(query)}&source=${activeTab}`
        : `${API_BASE}/ai-chats/search?q=${encodeURIComponent(query)}`;
      
      const response = await fetch(url);
      const data = await response.json();
      
      if (!data.error) {
        setConversations(data.results || []);
      }
    } catch (err) {
      console.error('Search failed:', err);
    }
  };

  // Handle tab change
  const handleTabChange = (tab: TabType) => {
    setActiveTab(tab);
    fetchConversations(tab === 'all' ? undefined : tab);
  };

  // Initial load
  useEffect(() => {
    fetchConversations();
    fetchStats();
  }, [fetchConversations, fetchStats]);

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return 'Unknown';
    const date = new Date(dateStr);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  const getProjectName = (path: string | null) => {
    if (!path) return 'No project';
    const parts = path.split(/[\\/]/);
    return parts[parts.length - 1] || path;
  };

  const filteredConversations = conversations;

  return (
    <div className="flex flex-col h-full bg-gray-900 text-gray-100">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-gray-700">
        <div className="flex items-center gap-2">
          <Bot className="w-6 h-6 text-purple-400" />
          <h2 className="text-xl font-semibold">AI Chat History</h2>
          <span className="text-xs text-gray-500 ml-2">(Claude • Codex • GPT)</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={syncAll}
            disabled={isSyncing}
            className="flex items-center gap-1 px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-md transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${isSyncing ? 'animate-spin' : ''}`} />
            {isSyncing ? 'Syncing...' : 'Sync All'}
          </button>
        </div>
      </div>

      {/* Source Stats Bar */}
      <div className="flex items-center gap-4 px-4 py-2 bg-gray-800/50 border-b border-gray-700">
        {Object.entries(stats).map(([source, count]) => (
          <div key={source} className="flex items-center gap-1 text-xs">
            {SOURCE_ICONS[source] || <MessageSquare className="w-3 h-3" />}
            <span className="capitalize">{source}:</span>
            <span className="font-medium">{count}</span>
          </div>
        ))}
        <div className="flex-1" />
        <div className="text-xs text-gray-500">
          Total: {Object.values(stats).reduce((a, b) => a + b, 0)} conversations
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-700">
        {(['all', 'claude', 'codex', 'gpt'] as TabType[]).map((tab) => (
          <button
            key={tab}
            onClick={() => handleTabChange(tab)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors
              ${activeTab === tab 
                ? 'border-blue-500 text-blue-400' 
                : 'border-transparent text-gray-400 hover:text-gray-200'
              }`}
          >
            <span className="flex items-center gap-1">
              {tab === 'all' && <Filter className="w-3 h-3" />}
              {tab === 'claude' && <Bot className="w-3 h-3" />}
              {tab === 'codex' && <Terminal className="w-3 h-3" />}
              {tab === 'gpt' && <Sparkles className="w-3 h-3" />}
              <span className="capitalize">{tab}</span>
              {tab !== 'all' && stats[tab] > 0 && (
                <span className="ml-1 text-xs bg-gray-700 px-1 rounded">{stats[tab]}</span>
              )}
            </span>
          </button>
        ))}
      </div>

      {/* Search bar */}
      <div className="p-4 border-b border-gray-700">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search across all AI conversations..."
            value={searchQuery}
            onChange={(e) => handleSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-gray-800 border border-gray-600 rounded-md text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      {/* Error message */}
      {error && (
        <div className="mx-4 mt-4 p-3 bg-red-900/50 border border-red-700 rounded-md text-sm text-red-200">
          {error}
        </div>
      )}

      {/* Conversations list */}
      <div className="flex-1 overflow-y-auto p-4">
        {isLoading ? (
          <div className="flex items-center justify-center h-32 text-gray-500">
            <RefreshCw className="w-5 h-5 animate-spin mr-2" />
            Loading...
          </div>
        ) : filteredConversations.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 text-gray-500">
            <MessageSquare className="w-8 h-8 mb-2 opacity-50" />
            <p>No conversations found</p>
            <button
              onClick={syncAll}
              className="mt-2 text-sm text-blue-400 hover:underline"
            >
              Sync now to import from AI tools
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            {filteredConversations.map((conv) => (
              <div
                key={conv.id}
                onClick={() => setSelectedConv(conv)}
                className={`p-3 rounded-md cursor-pointer transition-colors border
                  ${selectedConv?.id === conv.id 
                    ? 'bg-blue-900/30 border-blue-600' 
                    : 'bg-gray-800 border-gray-700 hover:border-gray-600'
                  }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-start gap-2 flex-1">
                    <div className="mt-0.5">
                      {SOURCE_ICONS[conv.source] || <MessageSquare className="w-4 h-4 text-gray-400" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <h3 className="text-sm font-medium text-gray-200 line-clamp-2">
                        {conv.title || 'Untitled conversation'}
                      </h3>
                      <div className="flex items-center gap-3 mt-1.5 text-xs text-gray-500">
                        <span className={`px-1.5 py-0.5 rounded border text-xs capitalize
                          ${SOURCE_COLORS[conv.source] || 'bg-gray-700 text-gray-300 border-gray-600'}`}>
                          {conv.source}
                        </span>
                        <span className="flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {formatDate(conv.started_at)}
                        </span>
                        {conv.project_path && (
                          <span className="flex items-center gap-1 truncate max-w-[150px]">
                            <Folder className="w-3 h-3" />
                            {getProjectName(conv.project_path)}
                          </span>
                        )}
                        <span className="text-gray-600">
                          {conv.message_count} messages
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="p-3 border-t border-gray-700 text-xs text-gray-500">
        <p>
          Read-only view of AI conversations. Original files remain in{' '}
          <code className="bg-gray-800 px-1 rounded">~/.claude/</code>,{' '}
          <code className="bg-gray-800 px-1 rounded">~/.codex/</code>
        </p>
      </div>
    </div>
  );
}

export default AIChatHistory;
