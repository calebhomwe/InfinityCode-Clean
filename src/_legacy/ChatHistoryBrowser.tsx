import { apiFetch } from "../lib/api";
/**
 * ChatHistoryBrowser - Browse and search Claude chat history in Infinity
 * 
 * This component displays a read-only copy of Claude's chat history that has
 * been synced to Infinity's chats.db. The original Claude files remain untouched.
 */

import { useState, useEffect, useCallback } from 'react';
import { Search, RefreshCw, MessageSquare, Clock, Folder, CheckCircle, AlertCircle } from 'lucide-react';
import { API_BASE } from '../lib/api';

interface Conversation {
  id: number;
  source_session_id: string;
  project_path: string;
  started_at: string;
  title: string;
  source_type: string;
  imported_at: string;
}

interface SyncStatus {
  claude_available: boolean;
  claude_hash: string;
  infinity_hash: string;
  in_sync: boolean;
  conversation_count: number;
  last_sync: string | null;
  error?: string;
}

export function ChatHistoryBrowser() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Conversation[] | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch sync status
  const fetchSyncStatus = useCallback(async () => {
    try {
      const response = await apiFetch(`${API_BASE}/claude-sync/status`);
      const data = await response.json();
      setSyncStatus(data);
    } catch (err) {
      console.error('Failed to fetch sync status:', err);
    }
  }, []);

  // Fetch conversations
  const fetchConversations = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await apiFetch(`${API_BASE}/claude-sync/chats?limit=100`);
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

  // Trigger sync
  const triggerSync = async () => {
    setIsSyncing(true);
    setError(null);
    try {
      const response = await apiFetch(`${API_BASE}/claude-sync/trigger`, {
        method: 'POST',
      });
      const data = await response.json();
      if (data.success) {
        await fetchSyncStatus();
        await fetchConversations();
      } else {
        setError(data.error || 'Sync failed');
      }
    } catch (err) {
      setError('Sync request failed');
      console.error(err);
    } finally {
      setIsSyncing(false);
    }
  };

  // Search conversations
  const handleSearch = async (query: string) => {
    setSearchQuery(query);
    if (!query.trim()) {
      setSearchResults(null);
      return;
    }
    try {
      const response = await apiFetch(`${API_BASE}/chats/search?q=${encodeURIComponent(query)}&limit=20`);
      const data = await response.json();
      if (data.error) {
        setSearchResults([]);
      } else {
        setSearchResults(data.results || []);
      }
    } catch (err) {
      console.error('Search failed:', err);
      setSearchResults([]);
    }
  };

  // Initial load
  useEffect(() => {
    fetchSyncStatus();
    fetchConversations();
  }, [fetchSyncStatus, fetchConversations]);

  const displayConversations = searchResults !== null ? searchResults : conversations;

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  const getProjectName = (path: string) => {
    if (!path) return 'No project';
    const parts = path.split(/[\\/]/);
    return parts[parts.length - 1] || path;
  };

  return (
    <div className="flex flex-col h-full bg-gray-900 text-gray-100">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-gray-700">
        <div className="flex items-center gap-2">
          <MessageSquare className="w-5 h-5 text-blue-400" />
          <h2 className="text-lg font-semibold">Claude History</h2>
          <span className="text-xs text-gray-500 ml-2">(read-only copy)</span>
        </div>
        <div className="flex items-center gap-2">
          {syncStatus && (
            <div className="flex items-center gap-1 text-xs">
              {syncStatus.in_sync ? (
                <CheckCircle className="w-4 h-4 text-green-400" />
              ) : (
                <AlertCircle className="w-4 h-4 text-yellow-400" />
              )}
              <span className={syncStatus.in_sync ? 'text-green-400' : 'text-yellow-400'}>
                {syncStatus.in_sync ? 'In sync' : 'Update available'}
              </span>
              <span className="text-gray-500">
                ({syncStatus.conversation_count} conversations)
              </span>
            </div>
          )}
          <button
            onClick={triggerSync}
            disabled={isSyncing}
            className="flex items-center gap-1 px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-md transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${isSyncing ? 'animate-spin' : ''}`} />
            {isSyncing ? 'Syncing...' : 'Sync Now'}
          </button>
        </div>
      </div>

      {/* Search bar */}
      <div className="p-4 border-b border-gray-700">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search conversations..."
            value={searchQuery}
            onChange={(e) => handleSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-gray-800 border border-gray-600 rounded-md text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
        {searchResults !== null && (
          <div className="mt-2 text-xs text-gray-400">
            {searchResults.length} results for "{searchQuery}"
            <button
              onClick={() => { setSearchQuery(''); setSearchResults(null); }}
              className="ml-2 text-blue-400 hover:underline"
            >
              Clear
            </button>
          </div>
        )}
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
        ) : displayConversations.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 text-gray-500">
            <MessageSquare className="w-8 h-8 mb-2 opacity-50" />
            <p>{searchResults !== null ? 'No results found' : 'No conversations synced yet'}</p>
            {searchResults === null && (
              <button
                onClick={triggerSync}
                className="mt-2 text-sm text-blue-400 hover:underline"
              >
                Sync now to import from Claude
              </button>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            {displayConversations.map((conv) => (
              <div
                key={conv.id}
                className="p-3 bg-gray-800 rounded-md hover:bg-gray-750 transition-colors border border-gray-700 hover:border-gray-600"
              >
                <div className="flex items-start justify-between gap-2">
                  <h3 className="text-sm font-medium text-gray-200 line-clamp-2 flex-1">
                    {conv.title || 'Untitled conversation'}
                  </h3>
                  <span className="text-xs text-gray-500 whitespace-nowrap">
                    {formatDate(conv.started_at)}
                  </span>
                </div>
                <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
                  <div className="flex items-center gap-1">
                    <Folder className="w-3 h-3" />
                    <span className="truncate max-w-[200px]">
                      {getProjectName(conv.project_path)}
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    <span>Imported {formatDate(conv.imported_at)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Footer info */}
      <div className="p-3 border-t border-gray-700 text-xs text-gray-500">
        <p>
          This is a read-only copy of your Claude chat history. Original files remain at{' '}
          <code className="bg-gray-800 px-1 rounded">~/.claude/</code>
        </p>
      </div>
    </div>
  );
}

export default ChatHistoryBrowser;
