"""Claude History Sync Module - Read-only sync from Claude to Infinity.

This module provides safe, read-only access to Claude's chat history files.
It NEVER writes to ~/.claude/ - only reads from it and writes to Infinity's chats.db.
"""

import json
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple


CLAUDE_HISTORY_FILE = Path.home() / ".claude" / "history.jsonl"
CLAUDE_SESSIONS_DIR = Path.home() / ".claude" / "sessions"
INFINITY_CHATS_DB = Path(__file__).parent.parent / "chats.db"


def get_file_hash(filepath: Path) -> str:
    """Calculate SHA256 hash of a file."""
    if not filepath.exists():
        return ""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def init_chats_db(db_path: Path = INFINITY_CHATS_DB) -> sqlite3.Connection:
    """Initialize the chats.db schema for Infinity's copy of Claude history."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Conversations table - stores metadata about each chat session
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_session_id TEXT UNIQUE NOT NULL,
            project_path TEXT,
            started_at TIMESTAMP,
            title TEXT,
            source_type TEXT DEFAULT 'claude',
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source_hash TEXT
        )
    """)
    
    # Messages table - stores individual messages
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,  -- 'user', 'assistant', 'system'
            content TEXT,
            timestamp TIMESTAMP,
            content_hash TEXT,
            tool_calls TEXT,  -- JSON array of tool calls
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )
    """)
    
    # Sync log - tracks when syncs occurred and what changed
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source_file TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            records_imported INTEGER DEFAULT 0,
            records_skipped INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success'
        )
    """)
    
    # Create indexes for faster queries
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(source_session_id)")
    
    conn.commit()
    return conn


def parse_history_jsonl(filepath: Path = CLAUDE_HISTORY_FILE) -> List[Dict]:
    """Parse Claude's history.jsonl file (read-only)."""
    entries = []
    if not filepath.exists():
        return entries
    
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)
            except json.JSONDecodeError:
                continue
    return entries


def get_last_sync_hash(db_path: Path = INFINITY_CHATS_DB) -> Optional[str]:
    """Get the hash of the last synced history file."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT source_hash FROM sync_log WHERE source_file = ? ORDER BY sync_time DESC LIMIT 1",
        (str(CLAUDE_HISTORY_FILE),)
    ).fetchone()
    conn.close()
    return row["source_hash"] if row else None


def sync_claude_history(db_path: Path = INFINITY_CHATS_DB, force: bool = False) -> Dict:
    """Sync Claude history to Infinity's chats.db (read-only from Claude).
    
    Args:
        db_path: Path to Infinity's chats.db
        force: If True, re-import even if hash matches
        
    Returns:
        Dict with sync results
    """
    # Ensure DB is initialized
    conn = init_chats_db(db_path)
    
    # Check if source exists
    if not CLAUDE_HISTORY_FILE.exists():
        return {
            "success": False,
            "error": f"Claude history file not found: {CLAUDE_HISTORY_FILE}",
            "records_imported": 0,
            "records_skipped": 0
        }
    
    # Calculate current file hash
    current_hash = get_file_hash(CLAUDE_HISTORY_FILE)
    last_hash = get_last_sync_hash(db_path)
    
    # Skip if unchanged (unless forced)
    if current_hash == last_hash and not force:
        return {
            "success": True,
            "status": "unchanged",
            "message": "No changes detected in Claude history",
            "records_imported": 0,
            "records_skipped": 0,
            "hash": current_hash
        }
    
    # Parse history entries
    entries = parse_history_jsonl()
    imported = 0
    skipped = 0
    
    for entry in entries:
        session_id = entry.get("sessionId")
        if not session_id:
            skipped += 1
            continue
        
        # Check if already exists
        existing = conn.execute(
            "SELECT id FROM conversations WHERE source_session_id = ?",
            (session_id,)
        ).fetchone()
        
        if existing:
            skipped += 1
            continue
        
        # Convert timestamp (ms to seconds)
        timestamp_ms = entry.get("timestamp", 0)
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000) if timestamp_ms else None
        
        # Insert conversation
        cursor = conn.execute(
            """INSERT INTO conversations 
               (source_session_id, project_path, started_at, title, source_hash)
               VALUES (?, ?, ?, ?, ?)""",
            (
                session_id,
                entry.get("project"),
                timestamp,
                entry.get("display", "")[:200],  # Truncate long titles
                current_hash
            )
        )
        conversation_id = cursor.lastrowid
        
        # Insert user message (from display field)
        if entry.get("display"):
            conn.execute(
                """INSERT INTO messages 
                   (conversation_id, role, content, timestamp, content_hash)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    conversation_id,
                    "user",
                    entry.get("display"),
                    timestamp,
                    get_file_hash(Path(str(entry.get("display"))))
                )
            )
        
        imported += 1
    
    # Log the sync
    conn.execute(
        """INSERT INTO sync_log 
           (source_file, source_hash, records_imported, records_skipped, status)
           VALUES (?, ?, ?, ?, ?)""",
        (str(CLAUDE_HISTORY_FILE), current_hash, imported, skipped, "success")
    )
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "status": "synced",
        "records_imported": imported,
        "records_skipped": skipped,
        "hash": current_hash,
        "previous_hash": last_hash
    }


def get_sync_status(db_path: Path = INFINITY_CHATS_DB) -> Dict:
    """Get current sync status between Claude and Infinity."""
    if not CLAUDE_HISTORY_FILE.exists():
        return {
            "claude_available": False,
            "in_sync": False,
            "error": "Claude history file not found"
        }
    
    current_hash = get_file_hash(CLAUDE_HISTORY_FILE)
    last_hash = get_last_sync_hash(db_path)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Count conversations
    row = conn.execute("SELECT COUNT(*) as count FROM conversations").fetchone()
    conversation_count = row["count"] if row else 0
    
    # Get last sync time
    row = conn.execute(
        "SELECT sync_time FROM sync_log ORDER BY sync_time DESC LIMIT 1"
    ).fetchone()
    last_sync = row["sync_time"] if row else None
    
    conn.close()
    
    return {
        "claude_available": True,
        "claude_hash": current_hash,
        "infinity_hash": last_hash,
        "in_sync": current_hash == last_hash,
        "conversation_count": conversation_count,
        "last_sync": last_sync
    }


def get_conversations(db_path: Path = INFINITY_CHATS_DB, limit: int = 100) -> List[Dict]:
    """Get list of synced conversations."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    rows = conn.execute(
        """SELECT * FROM conversations 
           ORDER BY started_at DESC 
           LIMIT ?""",
        (limit,)
    ).fetchall()
    
    conversations = [dict(row) for row in rows]
    conn.close()
    return conversations


def search_conversations(query: str, db_path: Path = INFINITY_CHATS_DB, limit: int = 20) -> List[Dict]:
    """Search conversations by content."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Search in both conversation titles and message content
    rows = conn.execute(
        """SELECT DISTINCT c.* FROM conversations c
           LEFT JOIN messages m ON m.conversation_id = c.id
           WHERE c.title LIKE ? OR m.content LIKE ?
           ORDER BY c.started_at DESC
           LIMIT ?""",
        (f"%{query}%", f"%{query}%", limit)
    ).fetchall()
    
    results = [dict(row) for row in rows]
    conn.close()
    return results


if __name__ == "__main__":
    # Test the sync
    print("Initializing chats.db schema...")
    init_chats_db()
    
    print("\nSyncing Claude history...")
    result = sync_claude_history()
    print(f"Result: {result}")
    
    print("\nSync status:")
    print(get_sync_status())
    
    print("\nConversations:")
    for conv in get_conversations(limit=5):
        print(f"  - {conv['started_at']}: {conv['title'][:60]}...")
