"""
Models for phase 4 project folder synchronization.

The app stores collaboration metadata locally. Syncthing remains the source of
truth for file transfer, device connections, and folder runtime state.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass
class Project:
    """A group-bound project workspace."""

    id: str = field(default_factory=lambda: _new_id("prj"))
    group_id: str = ""
    name: str = ""
    root_shared_folder_id: str = ""
    status: str = "reserved"
    created_by: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "group_id": self.group_id,
            "name": self.name,
            "root_shared_folder_id": self.root_shared_folder_id,
            "status": self.status,
            "created_by": self.created_by,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Project":
        return cls(
            id=data.get("id") or _new_id("prj"),
            group_id=data.get("group_id", ""),
            name=data.get("name", ""),
            root_shared_folder_id=data.get("root_shared_folder_id", ""),
            status=data.get("status", "reserved"),
            created_by=data.get("created_by", ""),
            metadata=data.get("metadata", {}) or {},
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


@dataclass
class SharedFolder:
    """A local directory bound to a group project and optionally Syncthing."""

    id: str = field(default_factory=lambda: _new_id("sf"))
    name: str = ""
    group_id: str = ""
    local_path: str = ""
    syncthing_folder_id: str = ""
    status: str = "reserved"
    project_id: str = ""
    folder_type: str = "root"
    last_status: str = ""
    last_completion: float = 0.0
    last_error: str = ""
    last_event_id: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "group_id": self.group_id,
            "local_path": self.local_path,
            "syncthing_folder_id": self.syncthing_folder_id,
            "status": self.status,
            "project_id": self.project_id,
            "folder_type": self.folder_type,
            "last_status": self.last_status,
            "last_completion": self.last_completion,
            "last_error": self.last_error,
            "last_event_id": self.last_event_id,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SharedFolder":
        return cls(
            id=data.get("id") or _new_id("sf"),
            name=data.get("name", ""),
            group_id=data.get("group_id", ""),
            local_path=data.get("local_path", ""),
            syncthing_folder_id=data.get("syncthing_folder_id", ""),
            status=data.get("status", "reserved"),
            project_id=data.get("project_id", ""),
            folder_type=data.get("folder_type", "root"),
            last_status=data.get("last_status", ""),
            last_completion=float(data.get("last_completion") or 0.0),
            last_error=data.get("last_error", ""),
            last_event_id=int(data.get("last_event_id") or 0),
            metadata=data.get("metadata", {}) or {},
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


@dataclass
class SyncDevice:
    """Local mapping between an app group member and a Syncthing device ID."""

    group_id: str
    user_id: str
    syncthing_device_id: str
    display_name: str = ""
    status: str = "manual"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_id": self.group_id,
            "user_id": self.user_id,
            "syncthing_device_id": self.syncthing_device_id,
            "display_name": self.display_name,
            "status": self.status,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncDevice":
        return cls(
            group_id=data.get("group_id", ""),
            user_id=data.get("user_id", ""),
            syncthing_device_id=data.get("syncthing_device_id", ""),
            display_name=data.get("display_name", ""),
            status=data.get("status", "manual"),
            metadata=data.get("metadata", {}) or {},
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


@dataclass
class FileAttachment:
    """Metadata for a file event message. File bytes are never stored here."""

    id: str = field(default_factory=lambda: _new_id("file"))
    message_id: str = ""
    file_name: str = ""
    size: int = 0
    mime_type: str = ""
    sha256: str = ""
    shared_folder_id: str = ""
    relative_path: str = ""
    sync_status: str = "reserved"
    event_type: str = ""
    project_id: str = ""
    origin_user_id: str = ""
    syncthing_event_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "file_name": self.file_name,
            "size": self.size,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "shared_folder_id": self.shared_folder_id,
            "relative_path": self.relative_path,
            "sync_status": self.sync_status,
            "event_type": self.event_type,
            "project_id": self.project_id,
            "origin_user_id": self.origin_user_id,
            "syncthing_event_id": self.syncthing_event_id,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileAttachment":
        return cls(
            id=data.get("id") or _new_id("file"),
            message_id=data.get("message_id", ""),
            file_name=data.get("file_name", ""),
            size=int(data.get("size") or 0),
            mime_type=data.get("mime_type", ""),
            sha256=data.get("sha256", ""),
            shared_folder_id=data.get("shared_folder_id", ""),
            relative_path=data.get("relative_path", ""),
            sync_status=data.get("sync_status", "reserved"),
            event_type=data.get("event_type", ""),
            project_id=data.get("project_id", ""),
            origin_user_id=data.get("origin_user_id", ""),
            syncthing_event_id=str(data.get("syncthing_event_id", "")),
            metadata=data.get("metadata", {}) or {},
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


FILE_EVENT_METADATA_SCHEMA: Dict[str, Any] = {
    "schema": "file_event_v1",
    "event_type": "",
    "project_id": "",
    "shared_folder_id": "",
    "syncthing_folder_id": "",
    "relative_path": "",
    "file_name": "",
    "size": 0,
    "mime_type": "",
    "sha256": "",
    "sync_status": "reserved",
    "origin_user_id": "",
    "event_time": 0.0,
}
