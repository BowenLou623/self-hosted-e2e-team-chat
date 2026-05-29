"""Local AI project assistant built on the phase 6 project index."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.ai.provider import AIProviderClient
from src.ai.settings import AISettings
from src.storage.sqlite_store import SQLiteStore
from src.sync.project_index_service import ProjectIndexService


TEXT_EXTENSIONS = {
    "c", "cc", "cpp", "css", "csv", "go", "h", "hpp", "html", "java", "js",
    "json", "jsx", "kt", "log", "md", "mjs", "mm", "py", "rb", "rs", "sh",
    "sql", "swift", "toml", "ts", "tsx", "txt", "xml", "yaml", "yml",
}


class ProjectAssistant:
    """Summarize indexed project metadata and explicit user-selected files."""

    def __init__(self, storage: SQLiteStore, settings: AISettings):
        self.storage = storage
        self.index = ProjectIndexService(storage)
        self.settings = settings
        self.provider = AIProviderClient(settings)

    def project_context(self, group_id: str = "", project_id: str = "") -> Dict[str, Any]:
        status = self.index.status(group_id=group_id)
        files = self.index.search(query="", group_id=group_id, limit=200, include_missing=False)
        if project_id:
            files = [item for item in files if item.get("project_id") == project_id]
        extension_counts: Dict[str, int] = {}
        total_size = 0
        for item in files:
            ext = item.get("extension") or "(none)"
            extension_counts[ext] = extension_counts.get(ext, 0) + 1
            total_size += int(item.get("size") or 0)
        recent_files = sorted(files, key=lambda item: float(item.get("updated_at") or 0), reverse=True)[:30]
        projects = [
            item for item in status.get("projects", [])
            if not project_id or item.get("project_id") == project_id
        ]
        return {
            "group_id": group_id,
            "project_id": project_id,
            "index_status": status,
            "projects": projects,
            "file_count": len(files),
            "total_size": total_size,
            "extension_counts": dict(sorted(extension_counts.items(), key=lambda item: (-item[1], item[0]))[:30]),
            "directory_summary": self._directory_summary(files),
            "recent_files": [
                {
                    "project_name": item.get("project_name", ""),
                    "group_name": item.get("group_name", ""),
                    "relative_path": item.get("relative_path", ""),
                    "extension": item.get("extension", ""),
                    "size": item.get("size", 0),
                    "updated_at": item.get("updated_at", 0),
                }
                for item in recent_files
            ],
            "context_policy": "metadata_only_until_user_selects_file",
        }

    def search_files(self, query: str, group_id: str = "", extension: str = "", limit: int = 30) -> List[Dict[str, Any]]:
        return self.index.search(query=query, group_id=group_id, extension=extension, limit=limit, include_missing=False)

    def summarize_project(
        self,
        group_id: str = "",
        project_id: str = "",
        include_file_snippets: bool = False,
        file_id: str = "",
    ) -> Dict[str, Any]:
        context = self.project_context(group_id=group_id, project_id=project_id)
        context["included_file_snippets"] = []
        if include_file_snippets:
            context["included_file_snippets"] = self._collect_file_snippets(
                context=context,
                group_id=group_id,
                project_id=project_id,
                file_id=file_id,
            )
            context["context_policy"] = "metadata_plus_limited_user_approved_file_snippets"
        prompt = (
            "请只根据下面提供的本地项目上下文，生成一段简短中文项目说明。"
            "metadata 是文件索引；included_file_snippets 只有在用户明确勾选时才会出现。"
            "不要假设未提供的文件内容，不要声称读取了所有文件。\n\n"
            + json.dumps(context, ensure_ascii=False, indent=2)
        )
        summary = self.provider.chat([
            {"role": "system", "content": "你是一个谨慎的本地项目助手，只基于提供的上下文回答。"},
            {"role": "user", "content": prompt},
        ])
        return {"summary": summary, "context": context}

    def summarize_file(self, file_id: str) -> Dict[str, Any]:
        file_info = self.index.locate(file_id)
        if not file_info:
            raise ValueError("indexed file not found")
        content = self._read_selected_text_file(file_info)
        prompt = (
            "请只根据用户显式选择的单个本地文件内容，生成简短中文摘要。"
            "如果内容被截断，请说明摘要只覆盖已提供片段。\n\n"
            f"文件 metadata:\n{json.dumps(self._safe_file_metadata(file_info), ensure_ascii=False, indent=2)}\n\n"
            f"文件内容:\n{content['text']}"
        )
        summary = self.provider.chat([
            {"role": "system", "content": "你是一个谨慎的本地项目助手，只基于提供的上下文回答。"},
            {"role": "user", "content": prompt},
        ])
        return {"summary": summary, "file": self._safe_file_metadata(file_info), "content": content}

    def _read_selected_text_file(self, file_info: Dict[str, Any]) -> Dict[str, Any]:
        max_bytes = max(1024, min(int(self.settings.max_file_bytes or 200 * 1024), 1024 * 1024))
        return self._read_text_file_limited(file_info, max_bytes=max_bytes)

    def _read_text_file_limited(self, file_info: Dict[str, Any], max_bytes: int) -> Dict[str, Any]:
        if not file_info.get("exists"):
            raise ValueError("indexed file is missing")
        extension = str(file_info.get("extension") or "").lower()
        mime_type = str(file_info.get("mime_type") or "")
        if extension not in TEXT_EXTENSIONS and not mime_type.startswith("text/"):
            raise ValueError("只支持摘要文本类文件")
        root = Path(file_info.get("root_path") or "").resolve(strict=False)
        path = Path(file_info.get("absolute_path") or "").resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("文件路径不在索引根目录内") from exc
        if path.is_symlink() or not path.exists() or not path.is_file():
            raise ValueError("文件不存在或不是普通文件")
        max_bytes = max(1024, min(int(max_bytes or 0), 1024 * 1024))
        size = os.path.getsize(path)
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
        truncated = len(raw) > max_bytes or size > max_bytes
        raw = raw[:max_bytes]
        return {
            "text": raw.decode("utf-8", errors="replace"),
            "bytes_read": len(raw),
            "truncated": truncated,
            "max_file_bytes": max_bytes,
        }

    def _directory_summary(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = {}
        for item in files:
            relative_path = str(item.get("relative_path") or "").strip("/")
            if not relative_path:
                continue
            parts = [part for part in relative_path.split("/") if part]
            if len(parts) <= 1:
                key = "(root)"
            else:
                key = "/".join(parts[:2]) if len(parts) > 2 else parts[0]
            counts[key] = counts.get(key, 0) + 1
        return [
            {"path": path, "file_count": count}
            for path, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:30]
        ]

    def _collect_file_snippets(
        self,
        context: Dict[str, Any],
        group_id: str = "",
        project_id: str = "",
        file_id: str = "",
    ) -> List[Dict[str, Any]]:
        snippet_limit = 16 * 1024
        total_budget = max(1024, min(int(self.settings.max_file_bytes or 200 * 1024), 1024 * 1024))
        candidates: List[Dict[str, Any]] = []

        if file_id:
            selected = self.index.locate(file_id)
            if selected:
                if group_id and selected.get("group_id") != group_id:
                    raise ValueError("选中文件不属于当前项目群组")
                if project_id and selected.get("project_id") != project_id:
                    raise ValueError("选中文件不属于当前项目")
                candidates = [selected]
        else:
            indexed_files = self.index.search(query="", group_id=group_id, limit=200, include_missing=False)
            if project_id:
                indexed_files = [item for item in indexed_files if item.get("project_id") == project_id]
            candidates = [
                item for item in indexed_files
                if self._is_small_text_candidate(item, max_size=snippet_limit)
            ][:5]

        snippets: List[Dict[str, Any]] = []
        remaining = total_budget
        for item in candidates:
            if remaining <= 0:
                break
            read_limit = min(snippet_limit, remaining)
            try:
                content = self._read_text_file_limited(item, max_bytes=read_limit)
            except ValueError:
                continue
            remaining -= int(content.get("bytes_read") or 0)
            snippets.append({
                "file": self._safe_file_metadata(item),
                "bytes_read": content["bytes_read"],
                "truncated": content["truncated"],
                "max_file_bytes": content["max_file_bytes"],
                "text": content["text"],
            })

        context["snippet_budget_bytes"] = total_budget
        context["snippet_per_file_limit_bytes"] = snippet_limit
        return snippets

    def _is_small_text_candidate(self, file_info: Dict[str, Any], max_size: int) -> bool:
        if not file_info.get("exists"):
            return False
        size = int(file_info.get("size") or 0)
        if size <= 0 or size > max_size:
            return False
        extension = str(file_info.get("extension") or "").lower()
        mime_type = str(file_info.get("mime_type") or "")
        return extension in TEXT_EXTENSIONS or mime_type.startswith("text/")

    def _safe_file_metadata(self, file_info: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": file_info.get("id", ""),
            "project_id": file_info.get("project_id", ""),
            "group_id": file_info.get("group_id", ""),
            "project_name": file_info.get("project_name", ""),
            "group_name": file_info.get("group_name", ""),
            "relative_path": file_info.get("relative_path", ""),
            "file_name": file_info.get("file_name", ""),
            "extension": file_info.get("extension", ""),
            "size": file_info.get("size", 0),
            "mime_type": file_info.get("mime_type", ""),
            "sha256": file_info.get("sha256", ""),
            "updated_at": file_info.get("updated_at", 0),
        }
