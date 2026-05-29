"""Reserved interfaces for future AI project understanding features.

The app intentionally does not implement AI summaries, embeddings, or model
calls yet. These protocols keep the future integration point explicit while
phase 6 provides reusable local file-index context.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


class FileLocator(Protocol):
    """Resolve project-relative file metadata to a local path."""

    def locate(self, project_id: str, relative_path: str) -> Optional[Path]:
        ...


class ProjectContextProvider(Protocol):
    """Provide local project context to a future AI layer."""

    def get_context(self, project_id: str) -> Dict[str, Any]:
        ...

    def list_recent_file_events(self, project_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        ...


class ProjectSummaryProvider(Protocol):
    """Future AI summary surface. No implementation is provided in phase 4."""

    def summarize(self, project_id: str) -> str:
        ...
