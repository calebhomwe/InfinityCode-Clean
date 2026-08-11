"""Claude Bridge - Export sessions and continue work in Infinity when Claude quota runs out.

This module provides:
1. Quota detection (detect when Claude credits are exhausted)
2. Session export (extract full conversation context from Claude)
3. Session continuation (allow Infinity to pick up where Claude left off)
"""

import json
import os
import re
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict


CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"
INFINITY_DIR = Path(__file__).parent.parent
BRIDGE_DB = INFINITY_DIR / "claude_bridge.db"


@dataclass
class ClaudeMessage:
    """Represents a single message in a Claude conversation."""
    role: str  # 'user', 'assistant', 'system'
    content: str
    timestamp: datetime
    tool_calls: Optional[List[Dict]] = None
    tool_results: Optional[List[Dict]] = None
    uuid: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "uuid": self.uuid,
        }


@dataclass
class ClaudeSession:
    """Represents a complete Claude session that can be migrated to Infinity."""
    session_id: str
    project_path: str
    started_at: datetime
    messages: List[ClaudeMessage]
    summary: Optional[str] = None
    source_file: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "project_path": self.project_path,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "messages": [m.to_dict() for m in self.messages],
            "summary": self.summary,
            "source_file": self.source_file,
        }
    
    def to_infinity_prompt(self) -> str:
        """Convert session to a prompt that Infinity can continue from."""
        lines = [
            "# Claude Session Continuation",
            f"Session ID: {self.session_id}",
            f"Project: {self.project_path}",
            f"Started: {self.started_at}",
            "",
            "## Conversation History",
            "",
        ]
        
        for msg in self.messages:
            role_display = msg.role.upper()
            lines.append(f"### {role_display}")
            lines.append(str(msg.content))
            lines.append("")
            
            if msg.tool_calls:
                lines.append("**Tool Calls:**")
                for tc in msg.tool_calls:
                    if isinstance(tc, dict):
                        tc_name = tc.get('name', 'unknown')
                        tc_args = tc.get('arguments', {})
                        lines.append(f"- {tc_name}: {tc_args}")
                    else:
                        lines.append(f"- {tc}")
                lines.append("")
        
        lines.append("---")
        lines.append("**Continuing from Claude...**")
        lines.append("")
        
        return "\n".join(lines)


def init_bridge_db(db_path: Path = BRIDGE_DB) -> sqlite3.Connection:
    """Initialize the bridge database for storing exported sessions."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exported_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            project_path TEXT,
            started_at TIMESTAMP,
            summary TEXT,
            full_context TEXT,  -- JSON serialized session
            exported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'exported',  -- 'exported', 'imported', 'continued'
            infinity_session_id TEXT  -- Links to Infinity session if continued
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS continuation_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exported_session_id INTEGER,
            prompt_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (exported_session_id) REFERENCES exported_sessions(id)
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON exported_sessions(session_id)
    """)
    
    conn.commit()
    return conn


def find_claude_project_files(project_path: str) -> List[Path]:
    """Find all conversation files for a given project."""
    project_safe = project_path.replace("\\", "-").replace("/", "-").replace(":", "")
    project_dir = CLAUDE_PROJECTS_DIR / f"C--{project_safe}"
    
    if not project_dir.exists():
        return []
    
    jsonl_files = list(project_dir.glob("*.jsonl"))
    return sorted(jsonl_files, key=lambda p: p.stat().st_mtime, reverse=True)


def parse_claude_conversation(jsonl_path: Path) -> Optional[ClaudeSession]:
    """Parse a Claude conversation JSONL file into a session object."""
    messages = []
    session_id = None
    project_path = None
    started_at = None
    
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                # Extract session metadata
                if not session_id:
                    session_id = entry.get("sessionId") or entry.get("promptId")
                if not project_path:
                    project_path = entry.get("cwd") or entry.get("project")
                
                # Parse timestamp
                ts = entry.get("timestamp")
                if ts and not started_at:
                    if isinstance(ts, (int, float)):
                        started_at = datetime.fromtimestamp(ts / 1000)
                    elif isinstance(ts, str):
                        try:
                            started_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except:
                            pass
                
                # Parse user messages
                if entry.get("type") == "user":
                    msg_data = entry.get("message", {})
                    content = msg_data.get("content", "")
                    if content:
                        messages.append(ClaudeMessage(
                            role="user",
                            content=content,
                            timestamp=started_at or datetime.now(),
                            uuid=entry.get("uuid"),
                        ))
                
                # Parse assistant messages (from completions)
                elif entry.get("type") == "assistant":
                    content = entry.get("content", "")
                    if content:
                        messages.append(ClaudeMessage(
                            role="assistant",
                            content=content,
                            timestamp=started_at or datetime.now(),
                            uuid=entry.get("uuid"),
                        ))
                
                # Parse tool calls
                elif entry.get("type") == "tool_call":
                    tool_calls = entry.get("tool_calls", [])
                    if tool_calls and messages:
                        messages[-1].tool_calls = tool_calls
                
                # Parse tool results
                elif entry.get("type") == "tool_result":
                    tool_results = entry.get("tool_results", [])
                    if tool_results and messages:
                        messages[-1].tool_results = tool_results
        
        if not session_id:
            session_id = jsonl_path.stem
        
        return ClaudeSession(
            session_id=session_id,
            project_path=project_path or str(jsonl_path.parent),
            started_at=started_at or datetime.now(),
            messages=messages,
            source_file=str(jsonl_path),
        )
    
    except Exception as e:
        print(f"Error parsing {jsonl_path}: {e}")
        return None


def export_session_for_infinity(
    session_id_or_path: str,
    db_path: Path = BRIDGE_DB
) -> Optional[Dict]:
    """Export a Claude session for import into Infinity.
    
    Args:
        session_id_or_path: Either a session ID or path to JSONL file
        db_path: Bridge database path
        
    Returns:
        Dict with export info or None if failed
    """
    conn = init_bridge_db(db_path)
    
    # Determine source
    jsonl_path = Path(session_id_or_path)
    if not jsonl_path.exists():
        # Find by session ID
        project_dirs = [d for d in CLAUDE_PROJECTS_DIR.iterdir() if d.is_dir()]
        for proj_dir in project_dirs:
            candidate = proj_dir / f"{session_id_or_path}.jsonl"
            if candidate.exists():
                jsonl_path = candidate
                break
    
    if not jsonl_path.exists():
        conn.close()
        return None
    
    # Parse the session
    session = parse_claude_conversation(jsonl_path)
    if not session:
        conn.close()
        return None
    
    # Generate continuation prompt
    continuation_prompt = session.to_infinity_prompt()
    
    # Store in bridge database
    try:
        started_at_str = session.started_at.isoformat() if session.started_at else None
        cursor = conn.execute(
            """INSERT OR REPLACE INTO exported_sessions 
               (session_id, project_path, started_at, summary, full_context, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                session.session_id,
                session.project_path,
                started_at_str,
                session.messages[-1].content[:200] if session.messages else "No messages",
                json.dumps(session.to_dict()),
                "exported",
            )
        )
        exported_id = cursor.lastrowid
        
        # Store continuation prompt
        conn.execute(
            """INSERT INTO continuation_prompts (exported_session_id, prompt_text)
               VALUES (?, ?)""",
            (exported_id, continuation_prompt)
        )
        
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "session_id": session.session_id,
            "project_path": session.project_path,
            "message_count": len(session.messages),
            "exported_id": exported_id,
            "continuation_preview": continuation_prompt[:500] + "...",
        }
    
    except Exception as e:
        conn.close()
        return {"success": False, "error": str(e)}


def get_exported_sessions(db_path: Path = BRIDGE_DB) -> List[Dict]:
    """Get all exported sessions ready for continuation in Infinity."""
    conn = init_bridge_db(db_path)
    
    rows = conn.execute(
        """SELECT * FROM exported_sessions 
           ORDER BY exported_at DESC"""
    ).fetchall()
    
    sessions = []
    for row in rows:
        sessions.append({
            "id": row["id"],
            "session_id": row["session_id"],
            "project_path": row["project_path"],
            "summary": row["summary"],
            "exported_at": row["exported_at"],
            "status": row["status"],
            "infinity_session_id": row["infinity_session_id"],
        })
    
    conn.close()
    return sessions


def get_continuation_prompt(exported_id: int, db_path: Path = BRIDGE_DB) -> Optional[str]:
    """Get the full continuation prompt for a specific exported session."""
    conn = init_bridge_db(db_path)
    
    row = conn.execute(
        """SELECT prompt_text FROM continuation_prompts
           WHERE exported_session_id = ?
           ORDER BY created_at DESC LIMIT 1""",
        (exported_id,)
    ).fetchone()
    
    conn.close()
    return row["prompt_text"] if row else None


def detect_quota_exhausted() -> bool:
    """Detect if Claude credits are exhausted based on recent errors.
    
    This checks for common quota error patterns in Claude's logs.
    In production, this would check actual Claude API responses.
    """
    # Check for quota error markers in Claude's log files
    log_files = [
        CLAUDE_DIR / "logs" / "error.log",
        CLAUDE_DIR / "logs" / "api.log",
    ]
    
    quota_patterns = [
        r"rate_limit",
        r"quota_exceeded",
        r"insufficient_quota",
        r"billing.*exceeded",
        r"429",
        r"credit.*exhausted",
    ]
    
    for log_file in log_files:
        if log_file.exists():
            try:
                content = log_file.read_text()
                for pattern in quota_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        return True
            except:
                pass
    
    # Check environment variable override (for testing)
    if os.getenv("CLAUDE_QUOTA_EXHAUSTED") == "1":
        return True
    
    return False


def create_fallback_prompt(project_path: str, last_query: str) -> str:
    """Create a fallback prompt when Claude quota is exhausted.
    
    This uses Infinity's own models to continue the work.
    """
    return f"""Claude quota has been exhausted. Switching to Infinity backup mode.

Project: {project_path}
Last user request: {last_query}

Please continue this work using Infinity's available models (OpenRouter/Qwen/DeepSeek).
Maintain the same quality and approach as Claude would.

Current context will be loaded from the exported Claude session.
"""


if __name__ == "__main__":
    # Test the bridge
    print("=== CLAUDE BRIDGE TEST ===\n")
    
    # Test 1: Find and export sessions
    print("1. Finding Claude sessions...")
    project_dirs = [d for d in CLAUDE_PROJECTS_DIR.iterdir() if d.is_dir()]
    print(f"   Found {len(project_dirs)} project directories")
    
    for proj_dir in project_dirs[:2]:
        jsonl_files = list(proj_dir.glob("*.jsonl"))
        print(f"   {proj_dir.name}: {len(jsonl_files)} conversation files")
        
        for jsonl_file in jsonl_files[:1]:
            print(f"\n   Exporting: {jsonl_file.name}")
            result = export_session_for_infinity(str(jsonl_file))
            if result and result.get("success"):
                print(f"   [OK] Exported {result.get('message_count', 0)} messages")
                print(f"   Session: {result.get('session_id', 'unknown')}")
            elif result:
                print(f"   [FAIL] {result.get('error', 'Export failed')}")
            else:
                print("   [FAIL] Export returned None")
    
    print("\n2. Listing exported sessions...")
    sessions = get_exported_sessions()
    for s in sessions[:3]:
        summary = s['summary'][:60] if s['summary'] else "No summary"
        # Clean non-ASCII for terminal
        summary = summary.encode('ascii', 'replace').decode('ascii')
        print(f"   - {s['session_id']}: {summary}...")
    
    print(f"\n3. Quota detection: {'EXHAUSTED' if detect_quota_exhausted() else 'OK'}")
    
    print("\n=== TEST COMPLETE ===")
