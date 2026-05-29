"""SQLite schema helpers for the local AI document library."""

from __future__ import annotations

import sqlite3


def create_ai_document_schema(conn: sqlite3.Connection) -> None:
    """Create profile-local Phase 9 AI document/RAG tables."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_document_sources (
            source_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL UNIQUE,
            project_id TEXT NOT NULL,
            group_id TEXT,
            relative_path TEXT NOT NULL,
            root_path_at_index TEXT,
            file_name TEXT,
            extension TEXT,
            sha256 TEXT,
            mtime_ns INTEGER DEFAULT 0,
            size INTEGER DEFAULT 0,
            mime_type TEXT,
            content_status TEXT NOT NULL,
            chunk_count INTEGER DEFAULT 0,
            last_error TEXT,
            indexed_at REAL DEFAULT (unixepoch()),
            updated_at REAL DEFAULT (unixepoch())
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_document_chunks (
            chunk_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            line_start INTEGER DEFAULT 1,
            line_end INTEGER DEFAULT 1,
            char_start INTEGER DEFAULT 0,
            char_end INTEGER DEFAULT 0,
            token_estimate INTEGER DEFAULT 0,
            content_sha256 TEXT,
            created_at REAL DEFAULT (unixepoch()),
            FOREIGN KEY(source_id) REFERENCES ai_document_sources(source_id)
                ON DELETE CASCADE,
            UNIQUE(source_id, chunk_index)
        )
        """
    )
    cursor.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS ai_document_chunks_fts USING fts5(
            relative_path,
            file_name,
            extension,
            text,
            chunk_id UNINDEXED,
            source_id UNINDEXED,
            project_id UNINDEXED,
            group_id UNINDEXED,
            file_id UNINDEXED
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_conversations (
            conversation_id TEXT PRIMARY KEY,
            profile TEXT,
            group_id TEXT,
            project_id TEXT,
            provider_type TEXT,
            model TEXT,
            title TEXT,
            created_at REAL DEFAULT (unixepoch()),
            updated_at REAL DEFAULT (unixepoch())
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_messages (
            message_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at REAL DEFAULT (unixepoch()),
            FOREIGN KEY(conversation_id) REFERENCES ai_conversations(conversation_id)
                ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_message_sources (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            source_index TEXT NOT NULL,
            file_id TEXT,
            source_id TEXT,
            chunk_id TEXT,
            relative_path TEXT,
            absolute_path TEXT,
            line_start INTEGER DEFAULT 1,
            line_end INTEGER DEFAULT 1,
            snippet TEXT,
            score REAL DEFAULT 0,
            sha256 TEXT,
            mtime_ns INTEGER DEFAULT 0,
            created_at REAL DEFAULT (unixepoch()),
            FOREIGN KEY(message_id) REFERENCES ai_messages(message_id)
                ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_embeddings (
            chunk_id TEXT NOT NULL,
            provider_type TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            dim INTEGER DEFAULT 0,
            vector_blob BLOB,
            content_sha256 TEXT,
            created_at REAL DEFAULT (unixepoch()),
            PRIMARY KEY(chunk_id, provider_type, embedding_model)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_document_sources_project
        ON ai_document_sources(project_id, group_id, content_status)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_document_sources_file
        ON ai_document_sources(file_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_document_chunks_source
        ON ai_document_chunks(source_id, chunk_index)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_conversations_scope
        ON ai_conversations(group_id, project_id, updated_at DESC)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation
        ON ai_messages(conversation_id, created_at)
        """
    )
