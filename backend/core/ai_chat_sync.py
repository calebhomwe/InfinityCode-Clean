"""Unified AI Chat Sync - Claude, Codex, GPT history in one place.

Reads from multiple AI tool directories and aggregates into Infinity's database.
All source files remain read-only; data is copied to Infinity's storage.
"""

import json
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any
from dataclasses import dataclass


# Source directories
CLAUDE_DIR = Path.home() / ".claude"
CODEX_DIR = Path.home() / ".codex"
OPENAI_DIR = Path.home() / ".openai"  # If exists
CHATGPT_DIR = Path.home() / ".chatgpt"  # If exists

# Infinity storage
INFINITY_DIR = Path(__file__).parent.parent
AI_CHATS_DB = INFINITY_DIR / "ai_chats.db"


@dataclass
class AIChatMessage:
    role: str  # 'user', 'assistant', 'system'
    content: str
    timestamp: Optional[datetime]
    tool_calls: Optional[List[Dict]] = None
    metadata: Optional[Dict] = None


@dataclass
class AIConversation:
    id: str
    source: str  # 'claude', 'codex', 'gpt', etc.
    title: str
    project_path: Optional[str]
    started_at: Optional[datetime]
    updated_at: Optional[datetime]
    messages: List[AIChatMessage]
    source_file: Optional[str] = None
    source_hash: Optional[str] = None


def init_ai_chats_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = db_path if db_path is not None else AI_CHATS_DB
    """Initialize the unified AI chats database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Conversations from all AI sources
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            source TEXT NOT NULL,  -- 'claude', 'codex', 'gpt', 'copilot', etc.
            title TEXT,
            project_path TEXT,
            started_at TIMESTAMP,
            updated_at TIMESTAMP,
            message_count INTEGER DEFAULT 0,
            source_file TEXT,
            source_hash TEXT,
            full_context TEXT,  -- JSON
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Messages table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            timestamp TIMESTAMP,
            tool_calls TEXT,  -- JSON
            metadata TEXT,  -- JSON
            FOREIGN KEY (conversation_id) REFERENCES ai_conversations(id) ON DELETE CASCADE
        )
    """)
    
    # Sync tracking per source
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_file TEXT,
            source_hash TEXT,
            records_imported INTEGER DEFAULT 0,
            sync_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_source ON ai_conversations(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_project ON ai_conversations(project_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_conv ON ai_messages(conversation_id)")
    
    conn.commit()
    return conn


# ==================== Claude Source ====================

def sync_claude_history(db_path: Optional[Path] = None) -> Dict:
    db_path = db_path if db_path is not None else AI_CHATS_DB
    """Sync Claude history to unified DB."""
    conn = init_ai_chats_db(db_path)
    imported = 0
    
    # Parse history.jsonl
    history_file = CLAUDE_DIR / "history.jsonl"
    if history_file.exists():
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        session_id = entry.get("sessionId", "unknown")
                        
                        # Check if exists
                        existing = conn.execute(
                            "SELECT id FROM ai_conversations WHERE source_id = ? AND source = ?",
                            (session_id, "claude")
                        ).fetchone()
                        
                        if existing:
                            continue
                        
                        # Parse timestamp
                        ts = entry.get("timestamp")
                        started_at = None
                        if ts:
                            started_at = datetime.fromtimestamp(ts / 1000)
                        
                        # Insert conversation
                        cursor = conn.execute(
                            """INSERT INTO ai_conversations 
                               (source_id, source, title, project_path, started_at, message_count)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                session_id,
                                "claude",
                                entry.get("display", "Untitled")[:200],
                                entry.get("project"),
                                started_at,
                                1
                            )
                        )
                        conv_id = cursor.lastrowid
                        
                        # Insert message
                        conn.execute(
                            """INSERT INTO ai_messages 
                               (conversation_id, role, content, timestamp)
                               VALUES (?, ?, ?, ?)""",
                            (conv_id, "user", entry.get("display", ""), started_at)
                        )
                        
                        imported += 1
                    except Exception as e:
                        print(f"Error parsing line: {e}")
                        continue
        except Exception as e:
            print(f"Error reading Claude history: {e}")
    
    # Log sync
    conn.execute(
        "INSERT INTO ai_sync_log (source, records_imported) VALUES (?, ?)",
        ("claude", imported)
    )
    
    conn.commit()
    conn.close()
    
    return {"source": "claude", "imported": imported}


# ==================== Codex Source ====================

def sync_codex_history(db_path: Optional[Path] = None) -> Dict:
    db_path = db_path if db_path is not None else AI_CHATS_DB
    """Sync Codex (OpenAI) history to unified DB."""
    conn = init_ai_chats_db(db_path)
    imported = 0
    
    # Parse Codex global state
    state_file = CODEX_DIR / ".codex-global-state.json"
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Look for project/thread data
            for key, value in data.items():
                if isinstance(value, dict) and "thread" in key.lower():
                    thread_id = key
                    
                    # Check existing
                    existing = conn.execute(
                        "SELECT id FROM ai_conversations WHERE source_id = ? AND source = ?",
                        (thread_id, "codex")
                    ).fetchone()
                    
                    if existing:
                        continue
                    
                    # Extract metadata
                    title = value.get("title", "Codex Session")
                    created = value.get("created_at")
                    started_at = None
                    if created:
                        try:
                            started_at = datetime.fromtimestamp(created / 1000)
                        except:
                            pass
                    
                    # Insert
                    conn.execute(
                        """INSERT INTO ai_conversations 
                           (source_id, source, title, started_at, message_count)
                           VALUES (?, ?, ?, ?, ?)""",
                        (thread_id, "codex", title[:200], started_at, 0)
                    )
                    imported += 1
        except Exception as e:
            print(f"Error reading Codex state: {e}")
    
    # Check archived sessions
    archived_dir = CODEX_DIR / "archived_sessions"
    if archived_dir.exists():
        for session_file in archived_dir.glob("*.json"):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    session = json.load(f)
                
                session_id = session.get("id") or session_file.stem
                
                # Check existing
                existing = conn.execute(
                    "SELECT id FROM ai_conversations WHERE source_id = ? AND source = ?",
                    (session_id, "codex")
                ).fetchone()
                
                if existing:
                    continue
                
                # Parse timestamps
                created = session.get("created_at")
                started_at = datetime.fromtimestamp(created / 1000) if created else None
                
                messages = session.get("messages", [])
                
                cursor = conn.execute(
                    """INSERT INTO ai_conversations 
                       (source_id, source, title, started_at, message_count, full_context)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        "codex",
                        session.get("title", "Archived Session")[:200],
                        started_at,
                        len(messages),
                        json.dumps(session)
                    )
                )
                conv_id = cursor.lastrowid
                
                # Insert messages
                for msg in messages:
                    conn.execute(
                        """INSERT INTO ai_messages 
                           (conversation_id, role, content, timestamp, metadata)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            conv_id,
                            msg.get("role", "user"),
                            msg.get("content", ""),
                            started_at,
                            json.dumps(msg.get("metadata", {}))
                        )
                    )
                
                imported += 1
            except Exception as e:
                print(f"Error reading archived session {session_file}: {e}")
                continue
    
    # Log sync
    conn.execute(
        "INSERT INTO ai_sync_log (source, records_imported) VALUES (?, ?)",
        ("codex", imported)
    )
    
    conn.commit()
    conn.close()
    
    return {"source": "codex", "imported": imported}


# ==================== GPT/OpenAI Source ====================

def sync_openai_history(db_path: Optional[Path] = None) -> Dict:
    db_path = db_path if db_path is not None else AI_CHATS_DB
    """Sync OpenAI/GPT chat history if available."""
    imported = 0
    
    # Check for OpenAI CLI config
    openai_config = Path.home() / ".openai" / "chat_history.json"
    if openai_config.exists():
        try:
            with open(openai_config, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            conn = init_ai_chats_db(db_path)
            
            for conv in data.get("conversations", []):
                # Process conversations
                pass  # Implementation depends on format
            
            conn.close()
        except Exception as e:
            print(f"Error reading OpenAI history: {e}")
    
    return {"source": "openai", "imported": imported}



# ==================== Qoder Source ====================

QODER_PROJECTS_DIR = Path.home() / ".qoder" / "cache" / "projects"


def _qoder_text_blocks(content: Any) -> List[str]:
    """Extract text blocks from Qoder's message.content[] structure.

    Qoder jsonl lines look like {"role": ..., "message": {"content":
    [{"type": "text", "text": ...}, {"type": "tool_use", ...}, ...]}}.
    Tool blocks are skipped - only conversational text is imported.
    """
    texts: List[str] = []
    if isinstance(content, str):
        if content.strip():
            texts.append(content)
        return texts
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if t and str(t).strip():
                    texts.append(str(t))
    return texts


def sync_qoder_history(db_path: Optional[Path] = None) -> Dict:
    db_path = db_path if db_path is not None else AI_CHATS_DB
    """Sync Qoder chat transcripts (conversation-history/*.jsonl) to the DB.

    Each transcript file becomes one conversation (title = the chat folder
    name, e.g. "chat-5-c8dfeaf9"); user/assistant text blocks become
    messages. Idempotent: deduped by sha256 of the file path.
    """
    conn = init_ai_chats_db(db_path)
    imported = 0
    if QODER_PROJECTS_DIR.exists():
        for jsonl in sorted(QODER_PROJECTS_DIR.glob("*/conversation-history/**/*.jsonl")):
            src_hash = hashlib.sha256(str(jsonl).encode("utf-8")).hexdigest()
            existing = conn.execute(
                "SELECT id FROM ai_conversations WHERE source_id = ? AND source = ?",
                (src_hash, "qoder")
            ).fetchone()
            if existing:
                continue
            conv_id = None
            try:
                lines = jsonl.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                role = str(entry.get("role", "user"))
                msg = entry.get("message")
                content = msg.get("content") if isinstance(msg, dict) else None
                texts = _qoder_text_blocks(content)
                if not texts:
                    continue
                if conv_id is None:
                    cursor = conn.execute(
                        """INSERT INTO ai_conversations
                           (source_id, source, title, source_file, source_hash, message_count)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (src_hash, "qoder", jsonl.parent.parent.parent.name[:200],
                         str(jsonl), src_hash, 0)
                    )
                    conv_id = cursor.lastrowid
                    imported += 1
                for t in texts:
                    conn.execute(
                        "INSERT INTO ai_messages (conversation_id, role, content) VALUES (?, ?, ?)",
                        (conv_id, role, t[:50000])
                    )
            if conv_id is not None:
                n = conn.execute(
                    "SELECT COUNT(*) FROM ai_messages WHERE conversation_id = ?", (conv_id,)
                ).fetchone()[0]
                conn.execute("UPDATE ai_conversations SET message_count = ? WHERE id = ?", (n, conv_id))
    conn.execute(
        "INSERT INTO ai_sync_log (source, records_imported) VALUES (?, ?)",
        ("qoder", imported)
    )
    conn.commit()
    conn.close()
    return {"source": "qoder", "imported": imported}


# ==================== OpenCode Source ====================

OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def sync_opencode_history(db_path: Optional[Path] = None) -> Dict:
    db_path = db_path if db_path is not None else AI_CHATS_DB
    """Sync OpenCode sessions (opencode.db SQLite) to the unified DB.

    Sessions become conversations (title/agent/model/project from the
    session row); text parts become messages. Read-only connection to the
    source DB; deduped by session id.
    """
    conn = init_ai_chats_db(db_path)
    imported = 0
    if not OPENCODE_DB.exists():
        conn.close()
        return {"source": "opencode", "imported": 0, "error": "no opencode db"}
    try:
        src = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row
    except Exception as exc:
        conn.close()
        return {"source": "opencode", "imported": 0, "error": str(exc)}

    def _ts(ms: Any) -> Optional[datetime]:
        try:
            return datetime.fromtimestamp(int(ms) / 1000) if ms else None
        except Exception:
            return None

    try:
        sessions = src.execute(
            "SELECT id, title, path, time_created, time_updated, time_archived "
            "FROM session ORDER BY time_updated DESC"
        ).fetchall()
        for s in sessions:
            if s["time_archived"]:
                continue
            sid = s["id"]
            existing = conn.execute(
                "SELECT id FROM ai_conversations WHERE source_id = ? AND source = ?",
                (sid, "opencode")
            ).fetchone()
            if existing:
                continue
            cursor = conn.execute(
                """INSERT INTO ai_conversations
                   (source_id, source, title, project_path, started_at, updated_at, message_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (sid, "opencode", (s["title"] or "OpenCode Session")[:200],
                 s["path"], _ts(s["time_created"]), _ts(s["time_updated"]), 0)
            )
            conv_id = cursor.lastrowid
            imported += 1
            n = 0
            msgs = src.execute(
                "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created",
                (sid,)
            ).fetchall()
            for m in msgs:
                try:
                    d = json.loads(m["data"])
                except Exception:
                    continue
                role = str(d.get("role", "user"))
                parts = src.execute(
                    "SELECT data FROM part WHERE message_id = ?", (m["id"],)
                ).fetchall()
                texts = []
                for p in parts:
                    try:
                        pd = json.loads(p["data"])
                    except Exception:
                        continue
                    if pd.get("type") == "text" and pd.get("text"):
                        texts.append(str(pd["text"]))
                if not texts:
                    continue
                for t in texts:
                    conn.execute(
                        "INSERT INTO ai_messages (conversation_id, role, content, timestamp) "
                        "VALUES (?, ?, ?, ?)",
                        (conv_id, role, t[:50000], _ts(d.get("time")))
                    )
                    n += 1
            conn.execute("UPDATE ai_conversations SET message_count = ? WHERE id = ?", (n, conv_id))
    finally:
        src.close()
    conn.execute(
        "INSERT INTO ai_sync_log (source, records_imported) VALUES (?, ?)",
        ("opencode", imported)
    )
    conn.commit()
    conn.close()
    return {"source": "opencode", "imported": imported}


# ==================== Unified API ====================

def sync_all_sources(db_path: Optional[Path] = None) -> Dict[str, Dict]:
    db_path = db_path if db_path is not None else AI_CHATS_DB
    """Sync all AI chat sources to unified DB."""
    results = {}
    
    # Initialize DB
    init_ai_chats_db(db_path)
    
    # Sync each source
    results["claude"] = sync_claude_history(db_path)
    results["codex"] = sync_codex_history(db_path)
    results["openai"] = sync_openai_history(db_path)
    results["qoder"] = sync_qoder_history(db_path)
    results["opencode"] = sync_opencode_history(db_path)
    
    return results


def get_all_conversations(
    source: Optional[str] = None,
    limit: int = 100,
    db_path: Optional[Path] = None
) -> List[Dict]:
    db_path = db_path if db_path is not None else AI_CHATS_DB
    """Get conversations from all AI sources."""
    conn = init_ai_chats_db(db_path)
    
    if source:
        rows = conn.execute(
            """SELECT * FROM ai_conversations 
               WHERE source = ?
               ORDER BY updated_at DESC, started_at DESC
               LIMIT ?""",
            (source, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM ai_conversations 
               ORDER BY updated_at DESC, started_at DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
    
    conversations = []
    for row in rows:
        conversations.append({
            "id": row["id"],
            "source_id": row["source_id"],
            "source": row["source"],
            "title": row["title"],
            "project_path": row["project_path"],
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
            "message_count": row["message_count"],
            "imported_at": row["imported_at"],
        })
    
    conn.close()
    return conversations


def get_conversation(conversation_id: int, db_path: Optional[Path] = None) -> Optional[Dict]:
    db_path = db_path if db_path is not None else AI_CHATS_DB
    """One conversation row by id, or None."""
    conn = init_ai_chats_db(db_path)
    row = conn.execute(
        "SELECT id, source_id, source, title, project_path, started_at, "
        "updated_at, message_count, imported_at FROM ai_conversations "
        "WHERE id = ?", (conversation_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_conversation_messages(conversation_id: int, db_path: Optional[Path] = None) -> List[Dict]:
    db_path = db_path if db_path is not None else AI_CHATS_DB
    """All messages of one conversation, oldest first."""
    conn = init_ai_chats_db(db_path)
    rows = conn.execute(
        "SELECT role, content, timestamp FROM ai_messages "
        "WHERE conversation_id = ? ORDER BY id",
        (conversation_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_all_conversations(
    query: str,
    source: Optional[str] = None,
    limit: int = 20,
    db_path: Optional[Path] = None
) -> List[Dict]:
    db_path = db_path if db_path is not None else AI_CHATS_DB
    """Search across all AI chat sources."""
    conn = init_ai_chats_db(db_path)
    
    sql = """
        SELECT DISTINCT c.* FROM ai_conversations c
        LEFT JOIN ai_messages m ON m.conversation_id = c.id
        WHERE (c.title LIKE ? OR m.content LIKE ?)
    """
    params = [f"%{query}%", f"%{query}%"]
    
    if source:
        sql += " AND c.source = ?"
        params.append(source)
    
    sql += " ORDER BY c.updated_at DESC, c.started_at DESC LIMIT ?"
    params.append(limit)
    
    rows = conn.execute(sql, params).fetchall()
    
    results = []
    for row in rows:
        results.append({
            "id": row["id"],
            "source_id": row["source_id"],
            "source": row["source"],
            "title": row["title"],
            "project_path": row["project_path"],
            "started_at": row["started_at"],
            "message_count": row["message_count"],
        })
    
    conn.close()
    return results


def get_source_stats(db_path: Optional[Path] = None) -> Dict[str, int]:
    db_path = db_path if db_path is not None else AI_CHATS_DB
    """Get conversation counts per source."""
    conn = init_ai_chats_db(db_path)
    
    stats = {}
    for row in conn.execute("SELECT source, COUNT(*) as count FROM ai_conversations GROUP BY source"):
        stats[row["source"]] = row["count"]
    
    conn.close()
    return stats


if __name__ == "__main__":
    # Test the sync
    print("=== AI Chat Sync Test ===\n")
    
    results = sync_all_sources()
    for source, result in results.items():
        print(f"{source}: {result['imported']} conversations imported")
    
    print("\nSource stats:")
    stats = get_source_stats()
    for source, count in stats.items():
        print(f"  - {source}: {count}")
    
    print("\nRecent conversations:")
    for conv in get_all_conversations(limit=5):
        print(f"  [{conv['source']}] {conv['title'][:50]}...")
