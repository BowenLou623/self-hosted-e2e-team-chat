"""Local multi-turn AI conversation storage."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.ai.settings import AISettings
from src.storage.ai_document_schema import create_ai_document_schema
from src.storage.sqlite_store import SQLiteStore


class ConversationStore:
    """Stores Phase 9 AI conversations in the active profile database."""

    def __init__(self, storage: SQLiteStore, profile: str = ""):
        self.storage = storage
        self.profile = profile
        self.db_path = Path(storage.db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            create_ai_document_schema(conn)
            conn.commit()
        finally:
            conn.close()

    def get_or_create(
        self,
        conversation_id: str = "",
        group_id: str = "",
        project_id: str = "",
        settings: Optional[AISettings] = None,
        title: str = "",
    ) -> Dict[str, Any]:
        if conversation_id:
            existing = self.get(conversation_id)
            if existing:
                return existing
        now = time.time()
        conversation_id = conversation_id or f"aiconv_{uuid.uuid4().hex}"
        provider_type = settings.provider_type if settings else ""
        model = settings.model if settings else ""
        if not title:
            title = "新对话"
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO ai_conversations
                (conversation_id, profile, group_id, project_id, provider_type, model, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, self.profile, group_id, project_id, provider_type, model, title[:80], now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get(conversation_id) or {}

    def get(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM ai_conversations WHERE conversation_id = ?", (conversation_id,))
            row = cursor.fetchone()
            return self._conversation_to_dict(row) if row else None
        finally:
            conn.close()

    def list(self, group_id: str = "", project_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 200))
        where = []
        params: List[Any] = []
        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        if project_id:
            where.append("project_id = ?")
            params.append(project_id)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT c.*,
                       (SELECT COUNT(*) FROM ai_messages m WHERE m.conversation_id = c.conversation_id) AS message_count
                FROM ai_conversations c
                {where_sql}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params + [limit],
            )
            return [self._conversation_to_dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def messages(self, conversation_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 500))
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM ai_messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (conversation_id, limit),
            )
            return [self._message_to_dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def recent_messages(self, conversation_id: str, turns: int = 6) -> List[Dict[str, Any]]:
        limit = max(2, min(int(turns or 6), 20) * 2)
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM ai_messages
                WHERE conversation_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            )
            rows = list(reversed(cursor.fetchall()))
            return [self._message_to_dict(row) for row in rows]
        finally:
            conn.close()

    def recent_user_questions(self, conversation_id: str, limit: int = 2) -> List[str]:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT content FROM ai_messages
                WHERE conversation_id = ? AND role = 'user'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (conversation_id, max(1, min(int(limit or 2), 5))),
            )
            return [str(row["content"] or "") for row in cursor.fetchall()]
        finally:
            conn.close()

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        message_id = f"aimsg_{uuid.uuid4().hex}"
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ai_messages
                (message_id, conversation_id, role, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, conversation_id, role, content, metadata_json, now),
            )
            if sources:
                for source in sources:
                    cursor.execute(
                        """
                        INSERT INTO ai_message_sources
                        (id, message_id, source_index, file_id, source_id, chunk_id,
                         relative_path, absolute_path, line_start, line_end, snippet,
                         score, sha256, mtime_ns, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"aimsgsrc_{uuid.uuid4().hex}",
                            message_id,
                            source.get("source_index", ""),
                            source.get("file_id", ""),
                            source.get("source_id", ""),
                            source.get("chunk_id", ""),
                            source.get("relative_path", ""),
                            source.get("absolute_path", ""),
                            int(source.get("line_start") or 1),
                            int(source.get("line_end") or 1),
                            source.get("snippet", ""),
                            float(source.get("score") or 0),
                            source.get("sha256", ""),
                            int(source.get("mtime_ns") or 0),
                            now,
                        ),
                    )
            cursor.execute(
                """
                UPDATE ai_conversations
                SET updated_at = ?,
                    title = CASE WHEN title = '新对话' AND ? = 'user'
                                 THEN substr(?, 1, 80)
                                 ELSE title END
                WHERE conversation_id = ?
                """,
                (now, role, content, conversation_id),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "created_at": now,
        }

    def delete(self, conversation_id: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM ai_conversations WHERE conversation_id = ?", (conversation_id,))
            conn.commit()
            return bool(cursor.rowcount)
        finally:
            conn.close()

    def clear(self, conversation_id: str) -> int:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM ai_messages WHERE conversation_id = ?", (conversation_id,))
            deleted = int(cursor.rowcount or 0)
            cursor.execute(
                "UPDATE ai_conversations SET updated_at = ?, title = '新对话' WHERE conversation_id = ?",
                (time.time(), conversation_id),
            )
            conn.commit()
            return deleted
        finally:
            conn.close()

    def _conversation_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "conversation_id": row["conversation_id"],
            "profile": row["profile"] or "",
            "group_id": row["group_id"] or "",
            "project_id": row["project_id"] or "",
            "provider_type": row["provider_type"] or "",
            "model": row["model"] or "",
            "title": row["title"] or "",
            "created_at": float(row["created_at"] or 0),
            "updated_at": float(row["updated_at"] or 0),
            "message_count": int(row["message_count"] or 0) if "message_count" in row.keys() else 0,
        }

    def _message_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        return {
            "message_id": row["message_id"],
            "conversation_id": row["conversation_id"],
            "role": row["role"],
            "content": row["content"],
            "metadata": metadata,
            "created_at": float(row["created_at"] or 0),
        }
