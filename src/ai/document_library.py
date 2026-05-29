"""Local document library, chunking, and FTS retrieval for Phase 9."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.ai.project_assistant import TEXT_EXTENSIONS
from src.ai.settings import AISettings
from src.storage.ai_document_schema import create_ai_document_schema
from src.storage.project_index_schema import create_project_index_schema
from src.storage.sqlite_store import SQLiteStore


CHUNK_TARGET_CHARS = 2200
CHUNK_HARD_LIMIT_CHARS = 3200
CHUNK_OVERLAP_CHARS = 300
CHUNK_OVERLAP_LINES = 12


class DocumentLibraryService:
    """Builds a profile-local searchable text library from indexed project files."""

    def __init__(self, storage: SQLiteStore, settings: Optional[AISettings] = None):
        self.storage = storage
        self.settings = settings or AISettings()
        self.db_path = Path(storage.db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            create_project_index_schema(conn)
            create_ai_document_schema(conn)
            conn.commit()
        finally:
            conn.close()

    def status(self, group_id: str = "", project_id: str = "") -> Dict[str, Any]:
        rows = self._indexed_file_rows(group_id=group_id, project_id=project_id, include_missing=True)
        source_by_file = self._sources_by_file(group_id=group_id, project_id=project_id)
        source_status_counts: Dict[str, int] = {}
        candidate_count = 0
        pending_count = 0
        stale_count = 0
        missing_count = 0
        chunk_count = 0
        deleted_local_count = 0
        total_size = 0
        latest_updated_at = 0.0

        seen_file_ids = set()
        for row in rows:
            file_id = str(row["id"])
            seen_file_ids.add(file_id)
            exists = bool(row["exists"])
            if exists:
                candidate_count += 1
                total_size += int(row["size"] or 0)
            source = source_by_file.get(file_id)
            if not source:
                if exists:
                    pending_count += 1
                continue
            status = str(source["content_status"] or "unknown")
            source_status_counts[status] = source_status_counts.get(status, 0) + 1
            chunk_count += int(source["chunk_count"] or 0)
            latest_updated_at = max(latest_updated_at, float(source["updated_at"] or 0))
            if status == "deleted_local":
                deleted_local_count += 1
                continue
            if status in {"pending", "restored"} and exists:
                pending_count += 1
            if not exists:
                missing_count += 1
            elif self._source_is_stale(row, source):
                stale_count += 1

        for file_id, source in source_by_file.items():
            if file_id in seen_file_ids:
                continue
            status = str(source["content_status"] or "unknown")
            source_status_counts[status] = source_status_counts.get(status, 0) + 1
            if status == "missing":
                missing_count += 1
            elif status == "deleted_local":
                deleted_local_count += 1

        return {
            "group_id": group_id,
            "project_id": project_id,
            "candidate_count": candidate_count,
            "source_count": len(source_by_file),
            "indexed_source_count": source_status_counts.get("indexed", 0),
            "chunk_count": chunk_count,
            "pending_count": pending_count,
            "stale_count": stale_count,
            "missing_count": missing_count,
            "skipped_count": source_status_counts.get("skipped", 0),
            "error_count": source_status_counts.get("error", 0),
            "deleted_local_count": deleted_local_count,
            "total_size": total_size,
            "last_updated_at": latest_updated_at,
            "source_status_counts": source_status_counts,
            "embedding_status": "reserved_disabled",
            "tables_ready": True,
        }

    def build(self, group_id: str = "", project_id: str = "") -> Dict[str, Any]:
        if not group_id:
            raise ValueError("请选择项目后再构建 AI 文档库")
        rows = self._indexed_file_rows(group_id=group_id, project_id=project_id, include_missing=True)
        now = time.time()
        summary = {
            "candidate_count": 0,
            "rebuilt_count": 0,
            "unchanged_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "missing_count": 0,
            "chunk_count": 0,
        }
        seen_file_ids = {str(row["id"]) for row in rows}
        source_by_file = self._sources_by_file(group_id=group_id, project_id=project_id)

        for row in rows:
            if bool(row["exists"]):
                summary["candidate_count"] += 1
            source = source_by_file.get(str(row["id"]))
            if not bool(row["exists"]):
                self._upsert_source_status(row, source, "missing", 0, "indexed file is missing", now)
                summary["missing_count"] += 1
                continue
            if source and str(source["content_status"] or "") == "deleted_local":
                summary["unchanged_count"] += 1
                continue
            if source and not self._source_is_stale(row, source) and source["content_status"] in {"indexed", "skipped"}:
                summary["unchanged_count"] += 1
                summary["chunk_count"] += int(source["chunk_count"] or 0)
                continue
            result = self._rebuild_file(row, source, now)
            summary["rebuilt_count"] += 1 if result["content_status"] == "indexed" else 0
            summary["skipped_count"] += 1 if result["content_status"] == "skipped" else 0
            summary["error_count"] += 1 if result["content_status"] == "error" else 0
            summary["chunk_count"] += int(result.get("chunk_count") or 0)

        for file_id, source in source_by_file.items():
            if file_id not in seen_file_ids:
                self._mark_source_missing(str(source["source_id"]), "source no longer exists in project index", now)
                summary["missing_count"] += 1

        status = self.status(group_id=group_id, project_id=project_id)
        return {"status": "ok" if summary["error_count"] == 0 else "partial", "summary": summary, "library": status}

    def search(
        self,
        query: str,
        group_id: str = "",
        project_id: str = "",
        limit: int = 20,
    ) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 20), 50))
        normalized_query = (query or "").strip()
        if not normalized_query:
            return {
                "query": "",
                "retrieval_mode": "fts_bm25",
                "results": self._recent_sources(group_id=group_id, project_id=project_id, limit=limit),
                "message": "empty_query_returns_recent_sources",
            }

        fts_query = self._fts_query(normalized_query)
        if not fts_query:
            return {"query": normalized_query, "retrieval_mode": "fts_bm25", "results": []}

        conn = self._connect()
        try:
            rows = self._search_fts(conn, fts_query, normalized_query, group_id, project_id, limit)
            if not rows:
                rows = self._search_like(conn, normalized_query, group_id, project_id, limit)
        except sqlite3.Error:
            rows = self._search_like(conn, normalized_query, group_id, project_id, limit)
        finally:
            conn.close()

        return {
            "query": normalized_query,
            "retrieval_mode": "fts_bm25",
            "results": rank_search_results(rows),
        }

    def retrieve_for_rag(
        self,
        query: str,
        group_id: str = "",
        project_id: str = "",
        limit: int = 8,
        per_file_limit: int = 3,
    ) -> Dict[str, Any]:
        raw = self.search(query, group_id=group_id, project_id=project_id, limit=max(limit * 3, 20))
        per_file_counts: Dict[str, int] = {}
        selected: List[Dict[str, Any]] = []
        for item in raw.get("results", []):
            if item.get("kind") == "source":
                continue
            file_id = str(item.get("file_id") or "")
            count = per_file_counts.get(file_id, 0)
            if count >= per_file_limit:
                continue
            per_file_counts[file_id] = count + 1
            selected.append(item)
            if len(selected) >= limit:
                break
        return {
            "query": query,
            "retrieval_mode": "fts_bm25",
            "requested_limit": limit,
            "results": selected,
            "candidate_count": len(raw.get("results", [])),
        }

    def list_sources(
        self,
        group_id: str = "",
        project_id: str = "",
        status: str = "",
        query: str = "",
        limit: int = 100,
    ) -> Dict[str, Any]:
        """List local AI document source records without touching real files."""
        limit = max(1, min(int(limit or 100), 500))
        where: List[str] = []
        params: List[Any] = []
        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        if project_id:
            where.append("project_id = ?")
            params.append(project_id)
        if status:
            where.append("content_status = ?")
            params.append(status)
        normalized_query = (query or "").strip().lower()
        if normalized_query:
            where.append("(lower(relative_path) LIKE ? OR lower(file_name) LIKE ? OR lower(last_error) LIKE ?)")
            like = f"%{normalized_query}%"
            params.extend([like, like, like])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT *
                FROM ai_document_sources
                {where_sql}
                ORDER BY updated_at DESC, relative_path ASC
                LIMIT ?
                """,
                params + [limit],
            )
            rows = [self._source_result(row) for row in cursor.fetchall()]
        finally:
            conn.close()
        return {
            "group_id": group_id,
            "project_id": project_id,
            "status": status,
            "query": query or "",
            "sources": rows,
            "count": len(rows),
            "real_files_deleted": False,
        }

    def delete_source(
        self,
        source_id: str = "",
        file_id: str = "",
        group_id: str = "",
        project_id: str = "",
    ) -> Dict[str, Any]:
        """Delete only this profile's local AI index rows for a source."""
        source = self._find_source(source_id=source_id, file_id=file_id, group_id=group_id, project_id=project_id)
        if not source:
            raise ValueError("AI document source not found")
        resolved_source_id = str(source["source_id"])
        self._delete_chunks(resolved_source_id)
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE ai_document_sources
                SET content_status = 'deleted_local',
                    chunk_count = 0,
                    last_error = 'deleted from local AI document library only',
                    updated_at = ?
                WHERE source_id = ?
                """,
                (time.time(), resolved_source_id),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "deleted": True,
            "source": self._source_result(self._find_source(source_id=resolved_source_id) or source),
            "real_file_deleted": False,
            "project_index_deleted": False,
            "scope": "local_profile_ai_index_only",
        }

    def restore_source(
        self,
        source_id: str = "",
        file_id: str = "",
        group_id: str = "",
        project_id: str = "",
    ) -> Dict[str, Any]:
        """Allow a previously deleted local AI source to be rebuilt."""
        source = self._find_source(source_id=source_id, file_id=file_id, group_id=group_id, project_id=project_id)
        if not source:
            raise ValueError("AI document source not found")
        resolved_source_id = str(source["source_id"])
        self._delete_chunks(resolved_source_id)
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE ai_document_sources
                SET content_status = 'pending',
                    chunk_count = 0,
                    last_error = '',
                    updated_at = ?
                WHERE source_id = ?
                """,
                (time.time(), resolved_source_id),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "restored": True,
            "source": self._source_result(self._find_source(source_id=resolved_source_id) or source),
            "needs_build": True,
            "real_file_deleted": False,
            "scope": "local_profile_ai_index_only",
        }

    def clear_group(self, group_id: str, project_id: str = "") -> Dict[str, Any]:
        """Hard-clear local AI document library rows for a group/project.

        Used by project unbind. This removes AI source/chunk/FTS metadata from
        the current profile database only and never deletes real project files.
        """
        normalized_group_id = (group_id or "").strip()
        normalized_project_id = (project_id or "").strip()
        if not normalized_group_id and not normalized_project_id:
            raise ValueError("group_id or project_id is required")

        where = []
        params: List[Any] = []
        if normalized_group_id:
            where.append("group_id = ?")
            params.append(normalized_group_id)
        if normalized_project_id:
            where.append("project_id = ?")
            params.append(normalized_project_id)
        where_sql = " AND ".join(where)

        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT source_id FROM ai_document_sources WHERE {where_sql}", params)
            source_ids = [str(row["source_id"]) for row in cursor.fetchall()]
            chunk_ids: List[str] = []
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                cursor.execute(
                    f"SELECT chunk_id FROM ai_document_chunks WHERE source_id IN ({placeholders})",
                    source_ids,
                )
                chunk_ids = [str(row["chunk_id"]) for row in cursor.fetchall()]

            deleted_embeddings = 0
            if chunk_ids:
                placeholders = ",".join("?" for _ in chunk_ids)
                cursor.execute(f"DELETE FROM ai_embeddings WHERE chunk_id IN ({placeholders})", chunk_ids)
                deleted_embeddings = int(cursor.rowcount or 0)

            deleted_chunks = 0
            deleted_fts = 0
            deleted_sources = 0
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                cursor.execute(f"DELETE FROM ai_document_chunks_fts WHERE source_id IN ({placeholders})", source_ids)
                deleted_fts += int(cursor.rowcount or 0)
                cursor.execute(f"DELETE FROM ai_document_chunks WHERE source_id IN ({placeholders})", source_ids)
                deleted_chunks = int(cursor.rowcount or 0)
                cursor.execute(f"DELETE FROM ai_document_sources WHERE source_id IN ({placeholders})", source_ids)
                deleted_sources = int(cursor.rowcount or 0)

            cursor.execute(f"DELETE FROM ai_document_chunks_fts WHERE {where_sql}", params)
            deleted_fts += int(cursor.rowcount or 0)
            conn.commit()
            return {
                "group_id": normalized_group_id,
                "project_id": normalized_project_id,
                "deleted_sources": deleted_sources,
                "deleted_chunks": deleted_chunks,
                "deleted_fts": deleted_fts,
                "deleted_embeddings": deleted_embeddings,
                "real_files_deleted": False,
                "scope": "local_profile_ai_document_library_only",
            }
        finally:
            conn.close()

    def diagnose(
        self,
        profile: str = "",
        group_id: str = "",
        project_id: str = "",
        query: str = "",
    ) -> Dict[str, Any]:
        """Return a no-provider-call report for the build/search/RAG chain."""
        normalized_query = (query or "").strip() or "README project"
        status = self.status(group_id=group_id, project_id=project_id)
        search = self.search(normalized_query, group_id=group_id, project_id=project_id, limit=8)
        rag = self.retrieve_for_rag(
            normalized_query,
            group_id=group_id,
            project_id=project_id,
            limit=int(self.settings.rag_max_chunks or 8),
            per_file_limit=3,
        )
        return {
            "profile": profile,
            "db_path": str(self.db_path),
            "group_id": group_id,
            "project_id": project_id,
            "query": normalized_query,
            "scope": self._diagnose_scope(group_id=group_id, project_id=project_id),
            "project_index": self._project_index_counts(group_id=group_id, project_id=project_id),
            "document_library": status,
            "tables": self._diagnose_table_counts(group_id=group_id, project_id=project_id),
            "fts_sync": self._diagnose_fts_sync(group_id=group_id, project_id=project_id),
            "search": {
                "retrieval_mode": search.get("retrieval_mode", "fts_bm25"),
                "result_count": len(search.get("results", [])),
                "top_results": [{key: value for key, value in item.items() if key != "text"} for item in search.get("results", [])[:5]],
            },
            "rag_preview": {
                "candidate_count": rag.get("candidate_count", 0),
                "source_count": len(rag.get("results", [])),
                "sources_available": bool(rag.get("results")),
            },
            "checks": self._diagnose_checks(status=status, search=search, rag=rag),
            "provider_called": False,
        }

    def _indexed_file_rows(self, group_id: str = "", project_id: str = "", include_missing: bool = False) -> List[sqlite3.Row]:
        where = []
        params: List[Any] = []
        if group_id:
            where.append("f.group_id = ?")
            params.append(group_id)
        if project_id:
            where.append("f.project_id = ?")
            params.append(project_id)
        if not include_missing:
            where.append('f."exists" = 1')
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT f.*, p.name AS project_name, g.name AS group_name
                FROM project_index_files f
                LEFT JOIN projects p ON p.id = f.project_id
                LEFT JOIN groups g ON g.id = f.group_id
                {where_sql}
                ORDER BY f.updated_at DESC, f.relative_path_lc ASC
                """,
                params,
            )
            return list(cursor.fetchall())
        finally:
            conn.close()

    def _sources_by_file(self, group_id: str = "", project_id: str = "") -> Dict[str, sqlite3.Row]:
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
                f"SELECT * FROM ai_document_sources {where_sql}",
                params,
            )
            return {str(row["file_id"]): row for row in cursor.fetchall()}
        finally:
            conn.close()

    def _find_source(
        self,
        source_id: str = "",
        file_id: str = "",
        group_id: str = "",
        project_id: str = "",
    ) -> Optional[sqlite3.Row]:
        where: List[str] = []
        params: List[Any] = []
        if source_id:
            where.append("source_id = ?")
            params.append(source_id)
        if file_id:
            where.append("file_id = ?")
            params.append(file_id)
        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        if project_id:
            where.append("project_id = ?")
            params.append(project_id)
        if not where or (not source_id and not file_id):
            return None
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM ai_document_sources WHERE {' AND '.join(where)} LIMIT 1",
                params,
            )
            return cursor.fetchone()
        finally:
            conn.close()

    def _source_result(self, row: sqlite3.Row) -> Dict[str, Any]:
        absolute_path = safe_absolute_path(Path(row["root_path_at_index"] or ""), str(row["relative_path"] or ""))
        return {
            "kind": "source",
            "source_id": row["source_id"],
            "file_id": row["file_id"],
            "project_id": row["project_id"],
            "group_id": row["group_id"],
            "relative_path": row["relative_path"],
            "absolute_path": str(absolute_path) if absolute_path else "",
            "file_name": row["file_name"] or "",
            "extension": row["extension"] or "",
            "size": int(row["size"] or 0),
            "sha256": row["sha256"] or "",
            "mtime_ns": int(row["mtime_ns"] or 0),
            "mime_type": row["mime_type"] or "",
            "content_status": row["content_status"] or "",
            "chunk_count": int(row["chunk_count"] or 0),
            "last_error": row["last_error"] or "",
            "indexed_at": float(row["indexed_at"] or 0),
            "updated_at": float(row["updated_at"] or 0),
            "real_file_deleted": False,
        }

    def _diagnose_scope(self, group_id: str, project_id: str) -> Dict[str, Any]:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            group = None
            project = None
            folder = None
            if group_id:
                cursor.execute("SELECT * FROM groups WHERE id = ?", (group_id,))
                group = cursor.fetchone()
                cursor.execute(
                    "SELECT * FROM shared_folders WHERE group_id = ? ORDER BY updated_at DESC LIMIT 1",
                    (group_id,),
                )
                folder = cursor.fetchone()
            if project_id:
                cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
                project = cursor.fetchone()
            elif group_id:
                cursor.execute("SELECT * FROM projects WHERE group_id = ? ORDER BY updated_at DESC LIMIT 1", (group_id,))
                project = cursor.fetchone()
            return {
                "group_exists": group is not None,
                "project_exists": project is not None,
                "shared_folder_exists": folder is not None,
                "group": row_to_plain_dict(group),
                "project": row_to_plain_dict(project),
                "shared_folder": row_to_plain_dict(folder),
                "project_matches_group": bool(
                    project is not None
                    and (not group_id or str(project["group_id"] or "") == group_id)
                    and (not project_id or str(project["id"] or "") == project_id)
                ),
                "local_path_exists": bool(folder and Path(str(folder["local_path"] or "")).is_dir()),
            }
        finally:
            conn.close()

    def _project_index_counts(self, group_id: str, project_id: str) -> Dict[str, Any]:
        where, params = scoped_where("f", group_id=group_id, project_id=project_id)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS total_count,
                    SUM(CASE WHEN f."exists" = 1 THEN 1 ELSE 0 END) AS existing_count,
                    SUM(CASE WHEN f."exists" = 0 THEN 1 ELSE 0 END) AS missing_count,
                    MAX(f.updated_at) AS last_updated_at
                FROM project_index_files f
                {where_sql}
                """,
                params,
            )
            counts = row_to_plain_dict(cursor.fetchone())
            return {
                "total_count": int((counts or {}).get("total_count") or 0),
                "existing_count": int((counts or {}).get("existing_count") or 0),
                "missing_count": int((counts or {}).get("missing_count") or 0),
                "last_updated_at": float((counts or {}).get("last_updated_at") or 0),
            }
        finally:
            conn.close()

    def _diagnose_table_counts(self, group_id: str, project_id: str) -> Dict[str, Any]:
        source_where, source_params = scoped_where("s", group_id=group_id, project_id=project_id)
        source_where_sql = f"WHERE {' AND '.join(source_where)}" if source_where else ""
        chunk_where_sql = f"WHERE {' AND '.join(source_where)}" if source_where else ""
        fts_where, fts_params = scoped_where("f", group_id=group_id, project_id=project_id)
        fts_where_sql = f"WHERE {' AND '.join(fts_where)}" if fts_where else ""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) AS count FROM ai_document_sources s {source_where_sql}", source_params)
            sources = int(cursor.fetchone()["count"] or 0)
            cursor.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM ai_document_chunks c
                JOIN ai_document_sources s ON s.source_id = c.source_id
                {chunk_where_sql}
                """,
                source_params,
            )
            chunks = int(cursor.fetchone()["count"] or 0)
            cursor.execute(f"SELECT COUNT(*) AS count FROM ai_document_chunks_fts f {fts_where_sql}", fts_params)
            fts = int(cursor.fetchone()["count"] or 0)
            return {
                "ai_document_sources": sources,
                "ai_document_chunks": chunks,
                "ai_document_chunks_fts": fts,
            }
        finally:
            conn.close()

    def _diagnose_fts_sync(self, group_id: str, project_id: str) -> Dict[str, Any]:
        source_where, source_params = scoped_where("s", group_id=group_id, project_id=project_id)
        source_where_sql = f"AND {' AND '.join(source_where)}" if source_where else ""
        fts_where, fts_params = scoped_where("f", group_id=group_id, project_id=project_id)
        fts_where_sql = f"WHERE {' AND '.join(fts_where)}" if fts_where else ""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM ai_document_chunks c
                JOIN ai_document_sources s ON s.source_id = c.source_id
                LEFT JOIN ai_document_chunks_fts f ON f.chunk_id = c.chunk_id
                WHERE f.chunk_id IS NULL
                {source_where_sql}
                """,
                source_params,
            )
            missing_fts = int(cursor.fetchone()["count"] or 0)
            cursor.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM ai_document_chunks_fts f
                LEFT JOIN ai_document_chunks c ON c.chunk_id = f.chunk_id
                {fts_where_sql}
                {"AND" if fts_where_sql else "WHERE"} c.chunk_id IS NULL
                """,
                fts_params,
            )
            orphan_fts = int(cursor.fetchone()["count"] or 0)
            cursor.execute(
                f"""
                SELECT COALESCE(SUM(s.chunk_count), 0) AS expected
                FROM ai_document_sources s
                {("WHERE " + " AND ".join(source_where)) if source_where else ""}
                """,
                source_params,
            )
            expected = int(cursor.fetchone()["expected"] or 0)
            cursor.execute(
                f"""
                SELECT COUNT(c.chunk_id) AS actual
                FROM ai_document_chunks c
                JOIN ai_document_sources s ON s.source_id = c.source_id
                {("WHERE " + " AND ".join(source_where)) if source_where else ""}
                """,
                source_params,
            )
            actual = int(cursor.fetchone()["actual"] or 0)
            return {
                "ok": missing_fts == 0 and orphan_fts == 0 and expected == actual,
                "missing_fts_rows": missing_fts,
                "orphan_fts_rows": orphan_fts,
                "source_chunk_count_total": expected,
                "actual_chunk_rows": actual,
            }
        finally:
            conn.close()

    def _diagnose_checks(
        self,
        status: Dict[str, Any],
        search: Dict[str, Any],
        rag: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        checks: List[Dict[str, str]] = []

        def add(key: str, state: str, message: str) -> None:
            checks.append({"key": key, "status": state, "message": message})

        add("tables_ready", "ok" if status.get("tables_ready") else "error", "AI document tables are available")
        add(
            "sources_present",
            "ok" if int(status.get("source_count") or 0) > 0 else "warning",
            f"{int(status.get('source_count') or 0)} source records",
        )
        add(
            "chunks_present",
            "ok" if int(status.get("chunk_count") or 0) > 0 else "warning",
            f"{int(status.get('chunk_count') or 0)} chunks",
        )
        add(
            "search_returns_results",
            "ok" if search.get("results") else "warning",
            f"{len(search.get('results', []))} search results",
        )
        add(
            "rag_has_sources",
            "ok" if rag.get("results") else "warning",
            f"{len(rag.get('results', []))} RAG sources",
        )
        if int(status.get("pending_count") or 0) > 0 or int(status.get("stale_count") or 0) > 0:
            add(
                "library_needs_build",
                "warning",
                f"pending={status.get('pending_count', 0)}, stale={status.get('stale_count', 0)}",
            )
        return checks

    def _source_is_stale(self, file_row: sqlite3.Row, source: sqlite3.Row) -> bool:
        if not bool(file_row["exists"]):
            return str(source["content_status"] or "") != "missing"
        return (
            int(source["mtime_ns"] or 0) != int(file_row["mtime_ns"] or 0)
            or int(source["size"] or 0) != int(file_row["size"] or 0)
            or str(source["sha256"] or "") != str(file_row["sha256"] or "")
        )

    def _rebuild_file(self, row: sqlite3.Row, source: Optional[sqlite3.Row], now: float) -> Dict[str, Any]:
        source_id = str(source["source_id"]) if source else f"aisrc_{uuid.uuid4().hex}"
        self._delete_chunks(source_id)
        try:
            content = self._read_indexed_text(row)
            chunks = chunk_text(content["text"])
            if not chunks:
                self._upsert_source(row, source_id, "skipped", 0, "empty text file", now)
                return {"content_status": "skipped", "chunk_count": 0}
            self._upsert_source(row, source_id, "indexed", len(chunks), "", now)
            self._insert_chunks(row, source_id, chunks)
            return {"content_status": "indexed", "chunk_count": len(chunks)}
        except SkippedDocument as exc:
            self._upsert_source(row, source_id, "skipped", 0, str(exc), now)
            return {"content_status": "skipped", "chunk_count": 0}
        except Exception as exc:
            self._upsert_source(row, source_id, "error", 0, str(exc), now)
            return {"content_status": "error", "chunk_count": 0}

    def _read_indexed_text(self, row: sqlite3.Row) -> Dict[str, Any]:
        if not bool(row["exists"]):
            raise SkippedDocument("indexed file is missing")
        extension = str(row["extension"] or "").lower()
        mime_type = str(row["mime_type"] or "")
        if extension not in TEXT_EXTENSIONS and not mime_type.startswith("text/"):
            raise SkippedDocument("not a text file")
        size = int(row["size"] or 0)
        if size <= 0:
            raise SkippedDocument("empty file")
        max_document_bytes = max(1024, min(int(self.settings.max_document_bytes or 1024 * 1024), 2 * 1024 * 1024))
        if size > max_document_bytes:
            raise SkippedDocument(f"file exceeds max_document_bytes={max_document_bytes}")
        root = Path(row["root_path_at_index"] or "").resolve(strict=False)
        relative_path = str(row["relative_path"] or "")
        if not relative_path or os.path.isabs(relative_path):
            raise ValueError("invalid relative path")
        path = (root / relative_path).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("file path escapes indexed root") from exc
        if path.is_symlink() or not path.exists() or not path.is_file():
            raise SkippedDocument("file is missing, symlink, or not a regular file")
        raw = path.read_bytes()
        if b"\x00" in raw[:8192]:
            raise SkippedDocument("binary file")
        return {
            "text": raw.decode("utf-8", errors="replace"),
            "bytes_read": len(raw),
        }

    def _upsert_source_status(
        self,
        row: sqlite3.Row,
        source: Optional[sqlite3.Row],
        status: str,
        chunk_count: int,
        last_error: str,
        now: float,
    ) -> None:
        source_id = str(source["source_id"]) if source else f"aisrc_{uuid.uuid4().hex}"
        self._delete_chunks(source_id)
        self._upsert_source(row, source_id, status, chunk_count, last_error, now)

    def _upsert_source(
        self,
        row: sqlite3.Row,
        source_id: str,
        status: str,
        chunk_count: int,
        last_error: str,
        now: float,
    ) -> None:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ai_document_sources
                (source_id, file_id, project_id, group_id, relative_path, root_path_at_index,
                 file_name, extension, sha256, mtime_ns, size, mime_type, content_status,
                 chunk_count, last_error, indexed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    group_id = excluded.group_id,
                    relative_path = excluded.relative_path,
                    root_path_at_index = excluded.root_path_at_index,
                    file_name = excluded.file_name,
                    extension = excluded.extension,
                    sha256 = excluded.sha256,
                    mtime_ns = excluded.mtime_ns,
                    size = excluded.size,
                    mime_type = excluded.mime_type,
                    content_status = excluded.content_status,
                    chunk_count = excluded.chunk_count,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    source_id,
                    row["id"],
                    row["project_id"],
                    row["group_id"],
                    row["relative_path"],
                    row["root_path_at_index"],
                    row["file_name"],
                    row["extension"],
                    row["sha256"] or "",
                    int(row["mtime_ns"] or 0),
                    int(row["size"] or 0),
                    row["mime_type"] or mimetypes.guess_type(str(row["file_name"] or ""))[0] or "",
                    status,
                    chunk_count,
                    last_error,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _mark_source_missing(self, source_id: str, last_error: str, now: float) -> None:
        self._delete_chunks(source_id)
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE ai_document_sources
                SET content_status = 'missing', chunk_count = 0, last_error = ?, updated_at = ?
                WHERE source_id = ?
                """,
                (last_error, now, source_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _delete_chunks(self, source_id: str) -> None:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT chunk_id FROM ai_document_chunks WHERE source_id = ?", (source_id,))
            chunk_ids = [str(row["chunk_id"]) for row in cursor.fetchall()]
            if chunk_ids:
                placeholders = ",".join("?" for _ in chunk_ids)
                cursor.execute(f"DELETE FROM ai_embeddings WHERE chunk_id IN ({placeholders})", chunk_ids)
            cursor.execute("DELETE FROM ai_document_chunks WHERE source_id = ?", (source_id,))
            cursor.execute("DELETE FROM ai_document_chunks_fts WHERE source_id = ?", (source_id,))
            conn.commit()
        finally:
            conn.close()

    def _insert_chunks(self, row: sqlite3.Row, source_id: str, chunks: List[Dict[str, Any]]) -> None:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            for item in chunks:
                chunk_id = f"aichunk_{uuid.uuid4().hex}"
                text = item["text"]
                content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
                cursor.execute(
                    """
                    INSERT INTO ai_document_chunks
                    (chunk_id, source_id, chunk_index, text, line_start, line_end,
                     char_start, char_end, token_estimate, content_sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        source_id,
                        int(item["chunk_index"]),
                        text,
                        int(item["line_start"]),
                        int(item["line_end"]),
                        int(item["char_start"]),
                        int(item["char_end"]),
                        estimate_tokens(text),
                        content_sha256,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO ai_document_chunks_fts
                    (relative_path, file_name, extension, text, chunk_id, source_id,
                     project_id, group_id, file_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["relative_path"],
                        row["file_name"],
                        row["extension"],
                        text,
                        chunk_id,
                        source_id,
                        row["project_id"],
                        row["group_id"],
                        row["id"],
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def _search_fts(
        self,
        conn: sqlite3.Connection,
        fts_query: str,
        original_query: str,
        group_id: str,
        project_id: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        where = ["s.content_status = 'indexed'", "ai_document_chunks_fts MATCH ?"]
        params: List[Any] = [fts_query]
        if group_id:
            where.append("s.group_id = ?")
            params.append(group_id)
        if project_id:
            where.append("s.project_id = ?")
            params.append(project_id)
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT
                bm25(ai_document_chunks_fts) AS rank,
                snippet(ai_document_chunks_fts, 3, '', '', '...', 24) AS fts_snippet,
                c.*,
                s.file_id,
                s.project_id,
                s.group_id,
                s.relative_path,
                s.root_path_at_index,
                s.file_name,
                s.extension,
                s.size,
                s.sha256,
                s.mtime_ns
            FROM ai_document_chunks_fts
            JOIN ai_document_chunks c ON c.chunk_id = ai_document_chunks_fts.chunk_id
            JOIN ai_document_sources s ON s.source_id = c.source_id
            WHERE {' AND '.join(where)}
            ORDER BY rank ASC
            LIMIT ?
            """,
            params + [limit],
        )
        return [self._chunk_result(row, original_query) for row in cursor.fetchall()]

    def _search_like(
        self,
        conn: sqlite3.Connection,
        query: str,
        group_id: str,
        project_id: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        terms = [term.lower() for term in tokenize_query(query)]
        if not terms:
            return []
        where = ["s.content_status = 'indexed'"]
        params: List[Any] = []
        if group_id:
            where.append("s.group_id = ?")
            params.append(group_id)
        if project_id:
            where.append("s.project_id = ?")
            params.append(project_id)
        for term in terms:
            like = f"%{term}%"
            where.append("(lower(c.text) LIKE ? OR lower(s.relative_path) LIKE ? OR lower(s.file_name) LIKE ?)")
            params.extend([like, like, like])
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT
                0.0 AS rank,
                substr(c.text, 1, 320) AS fts_snippet,
                c.*,
                s.file_id,
                s.project_id,
                s.group_id,
                s.relative_path,
                s.root_path_at_index,
                s.file_name,
                s.extension,
                s.size,
                s.sha256,
                s.mtime_ns
            FROM ai_document_chunks c
            JOIN ai_document_sources s ON s.source_id = c.source_id
            WHERE {' AND '.join(where)}
            ORDER BY s.updated_at DESC, c.chunk_index ASC
            LIMIT ?
            """,
            params + [limit],
        )
        return [self._chunk_result(row, query) for row in cursor.fetchall()]

    def _chunk_result(self, row: sqlite3.Row, query: str) -> Dict[str, Any]:
        root = Path(row["root_path_at_index"] or "")
        relative_path = str(row["relative_path"] or "")
        absolute_path = safe_absolute_path(root, relative_path)
        rank = float(row["rank"] or 0)
        path_boost = 0.0
        query_lc = query.lower()
        if query_lc and query_lc in relative_path.lower():
            path_boost += 2.0
        if query_lc and query_lc in str(row["file_name"] or "").lower():
            path_boost += 2.0
        snippet = str(row["fts_snippet"] or "")[:600]
        return {
            "kind": "chunk",
            "chunk_id": row["chunk_id"],
            "source_id": row["source_id"],
            "file_id": row["file_id"],
            "project_id": row["project_id"],
            "group_id": row["group_id"],
            "relative_path": relative_path,
            "absolute_path": str(absolute_path) if absolute_path else "",
            "file_name": row["file_name"] or "",
            "extension": row["extension"] or "",
            "size": int(row["size"] or 0),
            "sha256": row["sha256"] or "",
            "mtime_ns": int(row["mtime_ns"] or 0),
            "chunk_index": int(row["chunk_index"] or 0),
            "line_start": int(row["line_start"] or 1),
            "line_end": int(row["line_end"] or 1),
            "char_start": int(row["char_start"] or 0),
            "char_end": int(row["char_end"] or 0),
            "token_estimate": int(row["token_estimate"] or 0),
            "snippet": snippet,
            "text": row["text"] or "",
            "score": round((-rank) + path_boost, 6),
        }

    def _recent_sources(self, group_id: str, project_id: str, limit: int) -> List[Dict[str, Any]]:
        where = ["content_status = 'indexed'"]
        params: List[Any] = []
        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        if project_id:
            where.append("project_id = ?")
            params.append(project_id)
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT * FROM ai_document_sources
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC, relative_path ASC
                LIMIT ?
                """,
                params + [limit],
            )
            rows = cursor.fetchall()
        finally:
            conn.close()
        results = []
        for row in rows:
            absolute_path = safe_absolute_path(Path(row["root_path_at_index"] or ""), str(row["relative_path"] or ""))
            results.append({
                "kind": "source",
                "source_id": row["source_id"],
                "file_id": row["file_id"],
                "project_id": row["project_id"],
                "group_id": row["group_id"],
                "relative_path": row["relative_path"],
                "absolute_path": str(absolute_path) if absolute_path else "",
                "file_name": row["file_name"] or "",
                "extension": row["extension"] or "",
                "size": int(row["size"] or 0),
                "sha256": row["sha256"] or "",
                "mtime_ns": int(row["mtime_ns"] or 0),
                "line_start": 1,
                "line_end": 1,
                "snippet": "",
                "score": 0.0,
            })
        return results

    def _fts_query(self, query: str) -> str:
        terms = tokenize_query(query)
        return " OR ".join(f"{term}*" for term in terms[:8])


class SkippedDocument(Exception):
    """A file is intentionally not included in the local document library."""


def chunk_text(text: str) -> List[Dict[str, Any]]:
    normalized_lines = split_long_lines(text.splitlines(keepends=True))
    if not normalized_lines:
        return []
    line_starts: List[int] = []
    offset = 0
    for line, _line_number in normalized_lines:
        line_starts.append(offset)
        offset += len(line)

    chunks: List[Dict[str, Any]] = []
    start = 0
    chunk_index = 0
    while start < len(normalized_lines):
        end, _length = choose_chunk_end(normalized_lines, start)
        if end <= start:
            end = min(start + 1, len(normalized_lines))
        text_part = "".join(line for line, _line_number in normalized_lines[start:end]).strip()
        if text_part:
            line_start = int(normalized_lines[start][1])
            line_end = int(normalized_lines[end - 1][1])
            char_start = line_starts[start]
            char_end = line_starts[end - 1] + len(normalized_lines[end - 1][0])
            chunks.append({
                "chunk_index": chunk_index,
                "text": text_part,
                "line_start": line_start,
                "line_end": line_end,
                "char_start": char_start,
                "char_end": char_end,
            })
            chunk_index += 1
        if end >= len(normalized_lines):
            break
        start = next_start_with_overlap(normalized_lines, start, end)
    return chunks


def split_long_lines(lines: List[str]) -> List[Tuple[str, int]]:
    output: List[Tuple[str, int]] = []
    if not lines:
        return output
    for line_number, line in enumerate(lines, start=1):
        if len(line) <= CHUNK_HARD_LIMIT_CHARS:
            output.append((line, line_number))
            continue
        cursor = 0
        while cursor < len(line):
            output.append((line[cursor:cursor + CHUNK_HARD_LIMIT_CHARS], line_number))
            cursor += CHUNK_HARD_LIMIT_CHARS
    return output


def choose_chunk_end(lines: List[Tuple[str, int]], start: int) -> Tuple[int, int]:
    length = 0
    last_boundary: Optional[int] = None
    end = start
    while end < len(lines):
        line = lines[end][0]
        length += len(line)
        end += 1
        if length >= CHUNK_TARGET_CHARS and is_boundary(line):
            last_boundary = end
            break
        if length >= CHUNK_TARGET_CHARS and last_boundary is None and line.strip() == "":
            last_boundary = end
        if length >= CHUNK_HARD_LIMIT_CHARS:
            break
        if is_boundary(line):
            last_boundary = end
    if last_boundary and last_boundary > start and length >= CHUNK_TARGET_CHARS:
        return last_boundary, length
    return end, length


def next_start_with_overlap(lines: List[Tuple[str, int]], start: int, end: int) -> int:
    chars = 0
    count = 0
    cursor = end - 1
    while cursor > start and count < CHUNK_OVERLAP_LINES:
        line_len = len(lines[cursor][0])
        if chars + line_len > CHUNK_OVERLAP_CHARS:
            break
        chars += line_len
        count += 1
        cursor -= 1
    return max(start + 1, cursor + 1)


def is_boundary(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return bool(re.match(r"^(#{1,6}\s|class\s+|def\s+|struct\s+|enum\s+|func\s+|function\s+|import\s+|from\s+)", stripped))


def tokenize_query(query: str) -> List[str]:
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", query.lower())
    cleaned = []
    for token in tokens:
        safe = "".join(ch for ch in token if ch.isalnum() or ch == "_")
        if safe and safe not in cleaned:
            cleaned.append(safe)
    return cleaned


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / 4))


def row_to_plain_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    for key, value in list(data.items()):
        if isinstance(value, bytes):
            data[key] = value.decode("utf-8", errors="replace")
    return data


def scoped_where(alias: str, group_id: str = "", project_id: str = "") -> Tuple[List[str], List[Any]]:
    where: List[str] = []
    params: List[Any] = []
    if group_id:
        where.append(f"{alias}.group_id = ?")
        params.append(group_id)
    if project_id:
        where.append(f"{alias}.project_id = ?")
        params.append(project_id)
    return where, params


def rank_search_results(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        score = float(item.get("score") or 0)
        penalty = generated_noise_penalty(
            str(item.get("relative_path") or ""),
            str(item.get("file_name") or ""),
        )
        if penalty:
            item["score"] = round(score - penalty, 6)
            item["rank_note"] = "generated_or_index_file_demoted"
        ranked.append(item)
    return sorted(
        ranked,
        key=lambda item: (
            float(item.get("score") or 0),
            -int(item.get("chunk_index") or 0),
            str(item.get("relative_path") or ""),
        ),
        reverse=True,
    )


def generated_noise_penalty(relative_path: str, file_name: str) -> float:
    name = file_name.lower()
    path = relative_path.lower()
    if name in {"searchindex.js", "genindex.html", "objects.inv"}:
        return 8.0
    if name.endswith((".min.js", ".min.css")):
        return 6.0
    if "/_static/" in f"/{path}" and name.endswith((".js", ".css")):
        return 4.0
    if path.startswith("tmp/") and name.endswith((".json", ".js")):
        return 4.0
    return 0.0


def safe_absolute_path(root: Path, relative_path: str) -> Optional[Path]:
    if not relative_path or os.path.isabs(relative_path):
        return None
    candidate = (root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve(strict=False))
    except ValueError:
        return None
    return candidate
