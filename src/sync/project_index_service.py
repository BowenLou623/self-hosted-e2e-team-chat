"""Project file metadata indexing for Syncthing-backed workspaces."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.storage.project_index_schema import create_project_index_schema
from src.storage.sqlite_store import SQLiteStore
from src.sync.ai_interfaces import ProjectContextProvider


DEFAULT_SKIP_DIRS = {
    ".git",
    ".pytest_cache",
    ".stfolder",
    ".stversions",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
DEFAULT_MAX_HASH_BYTES = 50 * 1024 * 1024


class ProjectIndexService:
    """Indexes local project files into the active profile SQLite database."""

    def __init__(
        self,
        storage: SQLiteStore,
        max_hash_bytes: int = DEFAULT_MAX_HASH_BYTES,
        skip_dirs: Optional[Iterable[str]] = None,
    ):
        self.storage = storage
        self.db_path = Path(storage.db_path)
        self.max_hash_bytes = int(max_hash_bytes)
        self.skip_dirs = set(skip_dirs or DEFAULT_SKIP_DIRS)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            create_project_index_schema(conn)
            conn.commit()
        finally:
            conn.close()

    def scan(self, group_id: str = "") -> Dict[str, Any]:
        """Scan one group project or all bound project folders."""
        folders = self._list_bound_folders(group_id=group_id)
        runs: List[Dict[str, Any]] = []
        summary = {
            "folder_count": len(folders),
            "scanned_count": 0,
            "updated_count": 0,
            "deleted_count": 0,
            "skipped_count": 0,
            "error_count": 0,
        }

        for folder in folders:
            run = self._scan_folder(folder)
            runs.append(run)
            summary["scanned_count"] += int(run.get("scanned_count") or 0)
            summary["updated_count"] += int(run.get("updated_count") or 0)
            summary["deleted_count"] += int(run.get("deleted_count") or 0)
            summary["skipped_count"] += int(run.get("skipped_count") or 0)
            if run.get("status") == "failed":
                summary["error_count"] += 1

        return {
            "summary": summary,
            "runs": runs,
            "status": "ok" if summary["error_count"] == 0 else "partial",
        }

    def status(self, group_id: str = "") -> Dict[str, Any]:
        conn = self._connect()
        try:
            file_where = []
            file_params: List[Any] = []
            if group_id:
                file_where.append("group_id = ?")
                file_params.append(group_id)
            where_sql = f"WHERE {' AND '.join(file_where)}" if file_where else ""

            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS total_count,
                    SUM(CASE WHEN "exists" = 1 THEN 1 ELSE 0 END) AS existing_count,
                    SUM(CASE WHEN "exists" = 0 THEN 1 ELSE 0 END) AS missing_count,
                    MAX(updated_at) AS last_updated_at
                FROM project_index_files
                {where_sql}
                """,
                file_params,
            )
            counts = dict(cursor.fetchone() or {})

            run_where = []
            run_params: List[Any] = []
            if group_id:
                run_where.append("group_id = ?")
                run_params.append(group_id)
            run_where_sql = f"WHERE {' AND '.join(run_where)}" if run_where else ""
            cursor.execute(
                f"""
                SELECT * FROM project_index_runs
                {run_where_sql}
                ORDER BY started_at DESC
                LIMIT 1
                """,
                run_params,
            )
            last_run = cursor.fetchone()

            cursor.execute(
                f"""
                SELECT project_id, group_id, COUNT(*) AS file_count
                FROM project_index_files
                {where_sql}
                GROUP BY project_id, group_id
                ORDER BY file_count DESC
                """,
                file_params,
            )
            projects = [dict(row) for row in cursor.fetchall()]

            return {
                "total_count": int(counts.get("total_count") or 0),
                "existing_count": int(counts.get("existing_count") or 0),
                "missing_count": int(counts.get("missing_count") or 0),
                "last_updated_at": counts.get("last_updated_at") or 0,
                "last_run": self._run_to_dict(last_run) if last_run else None,
                "projects": projects,
                "tables_ready": True,
            }
        finally:
            conn.close()

    def search(
        self,
        query: str = "",
        group_id: str = "",
        extension: str = "",
        limit: int = 50,
        include_missing: bool = False,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 200))
        terms = [term.lower() for term in (query or "").split() if term.strip()]
        normalized_extension = extension.strip().lower().lstrip(".")

        where = []
        params: List[Any] = []
        if not include_missing:
            where.append('f."exists" = 1')
        if group_id:
            where.append("f.group_id = ?")
            params.append(group_id)
        if normalized_extension:
            where.append("f.extension = ?")
            params.append(normalized_extension)
        for term in terms:
            where.append("(f.file_name_lc LIKE ? OR f.relative_path_lc LIKE ?)")
            like = f"%{term}%"
            params.extend([like, like])

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
                ORDER BY f."exists" DESC, f.updated_at DESC, f.relative_path_lc ASC
                LIMIT ?
                """,
                params + [limit],
            )
            return [self._file_to_result(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def locate(self, file_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT f.*, p.name AS project_name, g.name AS group_name
                FROM project_index_files f
                LEFT JOIN projects p ON p.id = f.project_id
                LEFT JOIN groups g ON g.id = f.group_id
                WHERE f.id = ?
                """,
                (file_id,),
            )
            row = cursor.fetchone()
            return self._file_to_result(row) if row else None
        finally:
            conn.close()

    def locate_project_path(self, project_id: str, relative_path: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT f.*, p.name AS project_name, g.name AS group_name
                FROM project_index_files f
                LEFT JOIN projects p ON p.id = f.project_id
                LEFT JOIN groups g ON g.id = f.group_id
                WHERE f.project_id = ? AND f.relative_path = ? AND f."exists" = 1
                LIMIT 1
                """,
                (project_id, relative_path),
            )
            row = cursor.fetchone()
            return self._file_to_result(row) if row else None
        finally:
            conn.close()

    def clear_group(self, group_id: str, project_id: str = "") -> Dict[str, Any]:
        """Delete local project index rows for a group/project without touching files."""
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
            cursor.execute(f"DELETE FROM project_index_files WHERE {where_sql}", params)
            deleted_files = int(cursor.rowcount or 0)
            cursor.execute(f"DELETE FROM project_index_runs WHERE {where_sql}", params)
            deleted_runs = int(cursor.rowcount or 0)
            conn.commit()
            return {
                "group_id": normalized_group_id,
                "project_id": normalized_project_id,
                "deleted_files": deleted_files,
                "deleted_runs": deleted_runs,
                "real_files_deleted": False,
                "scope": "local_profile_project_index_only",
            }
        finally:
            conn.close()

    def _list_bound_folders(self, group_id: str = "") -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            params: List[Any] = []
            where = ["sf.local_path IS NOT NULL", "sf.local_path != ''"]
            if group_id:
                where.append("sf.group_id = ?")
                params.append(group_id)
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT
                    sf.id AS shared_folder_id,
                    sf.group_id,
                    sf.local_path,
                    sf.project_id AS folder_project_id,
                    p.id AS project_id,
                    p.name AS project_name
                FROM shared_folders sf
                LEFT JOIN projects p ON p.id = sf.project_id OR p.root_shared_folder_id = sf.id
                WHERE {' AND '.join(where)}
                ORDER BY sf.updated_at DESC
                """,
                params,
            )
            folders = []
            for row in cursor.fetchall():
                data = dict(row)
                data["project_id"] = data.get("project_id") or data.get("folder_project_id") or ""
                if data["project_id"]:
                    folders.append(data)
            return folders
        finally:
            conn.close()

    def _scan_folder(self, folder: Dict[str, Any]) -> Dict[str, Any]:
        run_id = f"idxrun_{uuid.uuid4().hex}"
        started_at = time.time()
        root_path = str(Path(folder["local_path"]).expanduser())
        run = {
            "id": run_id,
            "project_id": folder["project_id"],
            "group_id": folder["group_id"],
            "shared_folder_id": folder["shared_folder_id"],
            "root_path": root_path,
            "started_at": started_at,
            "completed_at": 0.0,
            "status": "running",
            "scanned_count": 0,
            "updated_count": 0,
            "deleted_count": 0,
            "skipped_count": 0,
            "error_summary": "",
        }
        self._insert_run(run)

        errors: List[str] = []
        root = Path(root_path)
        try:
            if not root.exists() or not root.is_dir():
                raise ValueError(f"project folder not found: {root_path}")
            root = root.resolve()
            run["root_path"] = str(root)

            for path in self._iter_files(root, errors):
                try:
                    result = self._index_file(folder, root, path, run_id)
                    run["scanned_count"] += 1
                    if result == "updated":
                        run["updated_count"] += 1
                except Exception as exc:
                    run["skipped_count"] += 1
                    if len(errors) < 20:
                        errors.append(f"{path}: {exc}")

            run["deleted_count"] = self._mark_missing(folder, run_id)
            run["status"] = "ok" if not errors else "partial"
        except Exception as exc:
            run["status"] = "failed"
            if len(errors) < 20:
                errors.append(str(exc))
        finally:
            run["completed_at"] = time.time()
            run["error_summary"] = "\n".join(errors[:20])
            self._finish_run(run)
        return run

    def _iter_files(self, root: Path, errors: List[str]) -> Iterable[Path]:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [
                name for name in dirnames
                if name not in self.skip_dirs and not Path(dirpath, name).is_symlink()
            ]
            for name in filenames:
                path = Path(dirpath, name)
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                except OSError as exc:
                    if len(errors) < 20:
                        errors.append(f"{path}: {exc}")
                    continue
                yield path

    def _index_file(self, folder: Dict[str, Any], root: Path, path: Path, scan_id: str) -> str:
        stat_result = path.stat()
        relative_path = path.relative_to(root).as_posix()
        now = time.time()
        size = int(stat_result.st_size)
        mtime = float(stat_result.st_mtime)
        mtime_ns = int(getattr(stat_result, "st_mtime_ns", int(mtime * 1_000_000_000)))
        file_name = path.name
        extension = path.suffix.lower().lstrip(".")
        mime_type = mimetypes.guess_type(file_name)[0] or ""
        file_kind = "directory" if path.is_dir() else "file"

        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM project_index_files
                WHERE project_id = ? AND shared_folder_id = ? AND relative_path = ?
                """,
                (folder["project_id"], folder["shared_folder_id"], relative_path),
            )
            existing = cursor.fetchone()
            if (
                existing
                and int(existing["size"] or 0) == size
                and int(existing["mtime_ns"] or 0) == mtime_ns
                and int(existing["exists"] or 0) == 1
            ):
                cursor.execute(
                    """
                    UPDATE project_index_files
                    SET last_seen_scan_id = ?, root_path_at_index = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (scan_id, str(root), now, existing["id"]),
                )
                conn.commit()
                return "unchanged"

            sha256 = ""
            hash_status = "skipped_large" if size > self.max_hash_bytes else "hashed"
            if hash_status == "hashed":
                try:
                    sha256 = self._sha256(path)
                except OSError:
                    hash_status = "hash_error"

            file_id = existing["id"] if existing else f"idxfile_{uuid.uuid4().hex}"
            indexed_at = float(existing["indexed_at"] or now) if existing else now
            cursor.execute(
                """
                INSERT INTO project_index_files
                (id, project_id, group_id, shared_folder_id, root_path_at_index,
                 relative_path, relative_path_lc, file_name, file_name_lc, extension,
                 size, mtime, mtime_ns, sha256, mime_type, file_kind, "exists",
                 hash_status, last_seen_scan_id, indexed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, shared_folder_id, relative_path)
                DO UPDATE SET
                    group_id = excluded.group_id,
                    root_path_at_index = excluded.root_path_at_index,
                    relative_path_lc = excluded.relative_path_lc,
                    file_name = excluded.file_name,
                    file_name_lc = excluded.file_name_lc,
                    extension = excluded.extension,
                    size = excluded.size,
                    mtime = excluded.mtime,
                    mtime_ns = excluded.mtime_ns,
                    sha256 = excluded.sha256,
                    mime_type = excluded.mime_type,
                    file_kind = excluded.file_kind,
                    "exists" = excluded."exists",
                    hash_status = excluded.hash_status,
                    last_seen_scan_id = excluded.last_seen_scan_id,
                    updated_at = excluded.updated_at
                """,
                (
                    file_id,
                    folder["project_id"],
                    folder["group_id"],
                    folder["shared_folder_id"],
                    str(root),
                    relative_path,
                    relative_path.lower(),
                    file_name,
                    file_name.lower(),
                    extension,
                    size,
                    mtime,
                    mtime_ns,
                    sha256,
                    mime_type,
                    file_kind,
                    1,
                    hash_status,
                    scan_id,
                    indexed_at,
                    now,
                ),
            )
            conn.commit()
            return "updated"
        finally:
            conn.close()

    def _mark_missing(self, folder: Dict[str, Any], scan_id: str) -> int:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE project_index_files
                SET "exists" = 0, updated_at = ?
                WHERE project_id = ?
                  AND shared_folder_id = ?
                  AND "exists" = 1
                  AND (last_seen_scan_id IS NULL OR last_seen_scan_id != ?)
                """,
                (time.time(), folder["project_id"], folder["shared_folder_id"], scan_id),
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        finally:
            conn.close()

    def _insert_run(self, run: Dict[str, Any]) -> None:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO project_index_runs
                (id, project_id, group_id, shared_folder_id, root_path, started_at,
                 completed_at, status, scanned_count, updated_count, deleted_count,
                 skipped_count, error_summary, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["id"],
                    run["project_id"],
                    run["group_id"],
                    run["shared_folder_id"],
                    run["root_path"],
                    run["started_at"],
                    run["completed_at"],
                    run["status"],
                    run["scanned_count"],
                    run["updated_count"],
                    run["deleted_count"],
                    run["skipped_count"],
                    run["error_summary"],
                    "{}",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _finish_run(self, run: Dict[str, Any]) -> None:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE project_index_runs
                SET root_path = ?, completed_at = ?, status = ?, scanned_count = ?,
                    updated_count = ?, deleted_count = ?, skipped_count = ?,
                    error_summary = ?, metadata = ?
                WHERE id = ?
                """,
                (
                    run["root_path"],
                    run["completed_at"],
                    run["status"],
                    run["scanned_count"],
                    run["updated_count"],
                    run["deleted_count"],
                    run["skipped_count"],
                    run["error_summary"],
                    json.dumps({"max_hash_bytes": self.max_hash_bytes}, sort_keys=True),
                    run["id"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _file_to_result(self, row: Optional[sqlite3.Row]) -> Dict[str, Any]:
        if row is None:
            return {}
        root = Path(row["root_path_at_index"] or "")
        relative_path = str(row["relative_path"] or "")
        absolute_path = self._safe_absolute_path(root, relative_path)
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "group_id": row["group_id"],
            "shared_folder_id": row["shared_folder_id"],
            "project_name": row["project_name"] if "project_name" in row.keys() else "",
            "group_name": row["group_name"] if "group_name" in row.keys() else "",
            "root_path": str(root),
            "relative_path": relative_path,
            "absolute_path": str(absolute_path) if absolute_path else "",
            "file_name": row["file_name"],
            "extension": row["extension"],
            "size": int(row["size"] or 0),
            "mtime": float(row["mtime"] or 0),
            "mtime_ns": int(row["mtime_ns"] or 0),
            "sha256": row["sha256"] or "",
            "mime_type": row["mime_type"] or "",
            "file_kind": row["file_kind"] or "file",
            "exists": bool(row["exists"]),
            "hash_status": row["hash_status"] or "",
            "indexed_at": float(row["indexed_at"] or 0),
            "updated_at": float(row["updated_at"] or 0),
        }

    def _run_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "group_id": row["group_id"],
            "shared_folder_id": row["shared_folder_id"],
            "root_path": row["root_path"],
            "started_at": float(row["started_at"] or 0),
            "completed_at": float(row["completed_at"] or 0),
            "status": row["status"],
            "scanned_count": int(row["scanned_count"] or 0),
            "updated_count": int(row["updated_count"] or 0),
            "deleted_count": int(row["deleted_count"] or 0),
            "skipped_count": int(row["skipped_count"] or 0),
            "error_summary": row["error_summary"] or "",
        }

    def _safe_absolute_path(self, root: Path, relative_path: str) -> Optional[Path]:
        if not relative_path or os.path.isabs(relative_path):
            return None
        candidate = (root / relative_path).resolve(strict=False)
        try:
            candidate.relative_to(root.resolve(strict=False))
        except ValueError:
            return None
        return candidate

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


class ProjectIndexContextProvider(ProjectContextProvider):
    """Reusable context provider for future AI project understanding."""

    def __init__(self, service: ProjectIndexService):
        self.service = service

    def locate(self, project_id: str, relative_path: str) -> Optional[Path]:
        result = self.service.locate_project_path(project_id, relative_path)
        if result:
            path = Path(result.get("absolute_path") or "")
            return path if path.exists() else None
        return None

    def get_context(self, project_id: str) -> Dict[str, Any]:
        status = self.service.status()
        recent_files = [
            item for item in self.service.search(query="", limit=50, include_missing=False)
            if item.get("project_id") == project_id
        ]
        return {
            "project_id": project_id,
            "index_status": status,
            "recent_files": recent_files,
            "capabilities": ["metadata_search", "file_location"],
            "ai_summary_available": False,
        }

    def list_recent_file_events(self, project_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        return [
            item for item in self.service.search(query="", limit=limit, include_missing=False)
            if item.get("project_id") == project_id
        ]
