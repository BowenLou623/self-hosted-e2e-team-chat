"""SQLite schema helpers for project file indexing."""

import sqlite3


def create_project_index_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS project_index_files (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            group_id TEXT,
            shared_folder_id TEXT NOT NULL,
            root_path_at_index TEXT,
            relative_path TEXT NOT NULL,
            relative_path_lc TEXT,
            file_name TEXT,
            file_name_lc TEXT,
            extension TEXT,
            size INTEGER DEFAULT 0,
            mtime REAL DEFAULT 0,
            mtime_ns INTEGER DEFAULT 0,
            sha256 TEXT,
            mime_type TEXT,
            file_kind TEXT,
            "exists" INTEGER DEFAULT 1,
            hash_status TEXT,
            last_seen_scan_id TEXT,
            indexed_at REAL DEFAULT (unixepoch()),
            updated_at REAL DEFAULT (unixepoch()),
            UNIQUE(project_id, shared_folder_id, relative_path)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS project_index_runs (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            group_id TEXT,
            shared_folder_id TEXT,
            root_path TEXT,
            started_at REAL DEFAULT (unixepoch()),
            completed_at REAL DEFAULT 0,
            status TEXT,
            scanned_count INTEGER DEFAULT 0,
            updated_count INTEGER DEFAULT 0,
            deleted_count INTEGER DEFAULT 0,
            skipped_count INTEGER DEFAULT 0,
            error_summary TEXT,
            metadata TEXT DEFAULT '{}'
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_project_index_files_project
        ON project_index_files(project_id, "exists")
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_project_index_files_group
        ON project_index_files(group_id, "exists")
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_project_index_files_name
        ON project_index_files(file_name_lc)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_project_index_files_path
        ON project_index_files(relative_path_lc)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_project_index_files_extension
        ON project_index_files(extension, "exists")
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_project_index_runs_project
        ON project_index_runs(project_id, started_at DESC)
        """
    )
