"""Application-level project sync service built around Syncthing."""

import hashlib
import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.models.group import Group
from src.models.sync import (
    FILE_EVENT_METADATA_SCHEMA,
    FileAttachment,
    Project,
    SharedFolder,
    SyncDevice,
)
from src.storage.sqlite_store import SQLiteStore
from src.sync.settings import SyncSettingsStore, SyncthingSettings
from src.sync.syncthing_client import SyncthingAPIError, SyncthingClient
from src.utils.logger import get_logger


SUPPORTED_FILE_EVENT_TYPES = {
    "LocalChangeDetected",
    "RemoteChangeDetected",
    "ItemFinished",
    "FolderSummary",
    "StateChanged",
}


class SyncService:
    """Coordinates local project metadata with Syncthing's REST API."""

    def __init__(
        self,
        storage: SQLiteStore,
        config_dir: str,
        current_user_id: str,
        client: Optional[SyncthingClient] = None,
        settings_store: Optional[SyncSettingsStore] = None,
    ):
        self.storage = storage
        self.current_user_id = current_user_id
        self.settings_store = settings_store or SyncSettingsStore(config_dir)
        self._client_override = client
        self.logger = get_logger("sync_service")

    def load_settings(self) -> SyncthingSettings:
        return self.settings_store.load()

    def save_settings(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> SyncthingSettings:
        return self.settings_store.save(base_url, api_key, timeout_seconds)

    def _client(self) -> SyncthingClient:
        if self._client_override is not None:
            return self._client_override
        return SyncthingClient.from_settings(self.load_settings())

    def detect_local_syncthing(self) -> Dict[str, Any]:
        """Return a UI-oriented Syncthing API status."""
        settings = self.load_settings()
        return self.test_syncthing_settings(settings)

    def test_syncthing_settings(self, settings: SyncthingSettings) -> Dict[str, Any]:
        """Test explicit Syncthing API settings without persisting them."""
        client = SyncthingClient.from_settings(settings)
        installed = self._is_syncthing_installed()

        if not settings.api_key:
            return {
                "state": "api_unconfigured",
                "base_url": settings.base_url,
                "installed": installed,
                "device_id": "",
                "version": "",
                "error": "Syncthing API Key 未配置",
                "error_code": "api_key_missing",
                "repair_hint": (
                    "打开 Syncthing Web UI，进入 Actions / Settings / GUI，复制 API Key 后填写到 Launcher。"
                ),
                "can_copy_device_id": False,
            }

        try:
            status = client.get_status()
            version = {}
            try:
                version = client.get_version()
            except SyncthingAPIError:
                version = {}
            return {
                "state": "connected",
                "base_url": settings.base_url,
                "device_id": str(status.get("myID") or status.get("myId") or ""),
                "version": version.get("version", ""),
                "status": status,
                "can_copy_device_id": bool(str(status.get("myID") or status.get("myId") or "")),
                "error_code": "",
                "repair_hint": "",
            }
        except SyncthingAPIError as e:
            return self._syncthing_error_status(settings, installed, e)

    def _syncthing_error_status(
        self,
        settings: SyncthingSettings,
        installed: bool,
        error: SyncthingAPIError,
    ) -> Dict[str, Any]:
        text = str(error)
        lowered = text.lower()

        if error.status_code == 403 and "csrf" in lowered:
            return {
                "state": "csrf_error",
                "base_url": settings.base_url,
                "installed": installed,
                "device_id": "",
                "version": "",
                "error": "HTTP 403 / CSRF 错误",
                "error_code": "csrf_forbidden",
                "repair_hint": "确认 API URL 指向 Syncthing GUI API，并使用 X-API-Key 对应的 GUI API Key。",
                "can_copy_device_id": False,
            }

        if error.status_code in {401, 403}:
            return {
                "state": "api_key_error",
                "base_url": settings.base_url,
                "installed": installed,
                "device_id": "",
                "version": "",
                "error": "API Key 错误或无权限",
                "error_code": "api_key_invalid",
                "repair_hint": "重新从 Syncthing Web UI 的 Actions / Settings / GUI 复制 API Key。",
                "can_copy_device_id": False,
            }

        not_running_markers = [
            "connection refused",
            "errno 61",
            "errno 111",
            "timed out",
            "timeout",
            "no route",
            "name or service not known",
        ]
        if any(marker in lowered for marker in not_running_markers):
            return {
                "state": "not_running",
                "base_url": settings.base_url,
                "installed": installed,
                "device_id": "",
                "version": "",
                "error": "Syncthing 未启动或 API URL 不可达",
                "error_code": "not_running",
                "repair_hint": "启动 Syncthing，并确认 API URL 默认为 http://127.0.0.1:8384。",
                "can_copy_device_id": False,
            }

        return {
            "state": "connection_failed" if installed else "not_installed",
            "base_url": settings.base_url,
            "installed": installed,
            "device_id": "",
            "version": "",
            "error": text,
            "error_code": "connection_failed",
            "repair_hint": "请确认 Syncthing 正在运行，API URL 正确，并且 Launcher 可以连接到该地址。",
            "can_copy_device_id": False,
        }

    def _is_syncthing_installed(self) -> bool:
        if shutil.which("syncthing"):
            return True
        mac_app_binary = Path("/Applications/Syncthing.app/Contents/MacOS/syncthing")
        return mac_app_binary.exists()

    def get_group_sync_overview(self, group_id: str) -> Dict[str, Any]:
        project = self.storage.get_project_by_group(group_id)
        folder = None
        if project and project.root_shared_folder_id:
            folder = self.storage.get_shared_folder(project.root_shared_folder_id)
        if folder is None:
            folder = self.storage.get_shared_folder_by_group(group_id)
        devices = self.storage.get_sync_devices_for_group(group_id)
        settings = self.load_settings()
        return {
            "group_id": group_id,
            "project": project.to_dict() if project else None,
            "shared_folder": folder.to_dict() if folder else None,
            "devices": [device.to_dict() for device in devices],
            "settings": settings.to_dict(mask_key=True),
            "configured": bool(folder and folder.syncthing_folder_id),
            "status": folder.last_status if folder and folder.last_status else (folder.status if folder else "unconfigured"),
            "completion": folder.last_completion if folder else 0.0,
            "error": folder.last_error if folder else "",
        }

    def bind_group_folder(
        self,
        group: Group,
        local_path: str,
        project_name: Optional[str] = None,
    ) -> Tuple[Project, SharedFolder]:
        path = Path(local_path or "").expanduser()
        if not path.exists() or not path.is_dir():
            raise ValueError("local_path must be an existing directory")
        absolute_path = str(path.resolve())

        project = self.storage.get_project_by_group(group.id)
        now = time.time()
        if project is None:
            project = Project(
                group_id=group.id,
                name=project_name or group.name or group.id,
                status="local_bound",
                created_by=self.current_user_id,
                created_at=now,
                updated_at=now,
                metadata={"phase": 4},
            )
        else:
            project.name = project_name or project.name or group.name or group.id
            project.status = "local_bound"
            project.updated_at = now

        folder = None
        if project.root_shared_folder_id:
            folder = self.storage.get_shared_folder(project.root_shared_folder_id)
        if folder is None:
            folder = self.storage.get_shared_folder_by_group(group.id)
        if folder is None:
            folder = SharedFolder(
                name=project.name,
                group_id=group.id,
                local_path=absolute_path,
                status="local_bound",
                project_id=project.id,
                folder_type="root",
                metadata={"bound_by": self.current_user_id},
                created_at=now,
                updated_at=now,
            )
        else:
            folder.name = project.name
            folder.group_id = group.id
            folder.local_path = absolute_path
            folder.status = "local_bound" if not folder.syncthing_folder_id else "configured"
            folder.project_id = project.id
            folder.folder_type = folder.folder_type or "root"
            folder.updated_at = now

        self.storage.save_shared_folder(folder)
        project.root_shared_folder_id = folder.id
        self.storage.save_project(project)
        return project, folder

    def configure_syncthing_folder(self, group_id: str) -> Dict[str, Any]:
        project = self.storage.get_project_by_group(group_id)
        folder = self.storage.get_shared_folder_by_group(group_id)
        if folder is None or not folder.local_path:
            raise ValueError("group has no local shared folder")

        folder_id = folder.syncthing_folder_id or self._build_syncthing_folder_id(group_id, folder.id)
        devices = self.storage.get_sync_devices_for_group(group_id)
        client = self._client()

        for device in devices:
            client.upsert_device(device.syncthing_device_id, device.display_name or device.user_id)

        client.upsert_folder(
            folder_id=folder_id,
            path=folder.local_path,
            label=(project.name if project else folder.name) or group_id,
            device_ids=[device.syncthing_device_id for device in devices],
        )
        restart_required = client.get_restart_required()

        folder.syncthing_folder_id = folder_id
        folder.status = "configured"
        folder.last_status = "configured"
        folder.last_error = ""
        folder.metadata = {
            **(folder.metadata or {}),
            "restart_required": restart_required,
            "configured_by": self.current_user_id,
            "configured_at": time.time(),
        }
        self.storage.save_shared_folder(folder)

        if project is not None:
            project.status = "configured"
            project.updated_at = time.time()
            self.storage.save_project(project)

        overview = self.get_group_sync_overview(group_id)
        overview["restart_required"] = restart_required
        return overview

    def stop_group_sync(self, group_id: str) -> Dict[str, Any]:
        """Stop this client's Syncthing folder sharing while keeping app metadata."""
        project = self.storage.get_project_by_group(group_id)
        folder = self.storage.get_shared_folder_by_group(group_id)
        if folder is None:
            overview = self.get_group_sync_overview(group_id)
            overview["status"] = "unconfigured"
            return overview

        now = time.time()
        previous_folder_id = folder.syncthing_folder_id
        delete_result: Dict[str, Any] = {}
        restart_required = False
        restart_check_error = ""

        if previous_folder_id:
            delete_result = self._client().delete_folder(previous_folder_id)
            try:
                restart_required = self._client().get_restart_required()
            except Exception as e:
                restart_check_error = str(e)

        folder.syncthing_folder_id = ""
        folder.status = "stopped"
        folder.last_status = "stopped"
        folder.last_completion = 0.0
        folder.last_error = ""
        folder.updated_at = now
        folder.metadata = {
            **(folder.metadata or {}),
            "last_syncthing_folder_id": previous_folder_id,
            "stopped_at": now,
            "stopped_by": self.current_user_id,
            "restart_required": restart_required,
            "restart_check_error": restart_check_error,
            "delete_result": delete_result,
        }
        self.storage.save_shared_folder(folder)

        if project is not None:
            project.status = "stopped"
            project.updated_at = now
            project.metadata = {
                **(project.metadata or {}),
                "stopped_at": now,
                "stopped_by": self.current_user_id,
            }
            self.storage.save_project(project)

        overview = self.get_group_sync_overview(group_id)
        overview["restart_required"] = restart_required
        overview["delete_result"] = delete_result
        return overview

    def unbind_group_project(self, group_id: str, local_only: bool = False) -> Dict[str, Any]:
        """Remove this profile's project sync binding without deleting real files."""
        normalized_group_id = (group_id or "").strip()
        if not normalized_group_id:
            raise ValueError("group_id is required")

        project = self.storage.get_project_by_group(normalized_group_id)
        folder = self.storage.get_shared_folder_by_group(normalized_group_id)
        project_id = project.id if project else (folder.project_id if folder else "")
        local_path = folder.local_path if folder else ""
        previous_folder_id = folder.syncthing_folder_id if folder else ""

        syncthing_delete_result: Dict[str, Any] = {}
        restart_required = False
        restart_check_error = ""
        if previous_folder_id and not local_only:
            syncthing_delete_result = self._client().delete_folder(previous_folder_id)
            try:
                restart_required = self._client().get_restart_required()
            except Exception as exc:
                restart_check_error = str(exc)

        from src.ai.document_library import DocumentLibraryService
        from src.sync.project_index_service import ProjectIndexService

        project_index_clear = ProjectIndexService(self.storage).clear_group(
            group_id=normalized_group_id,
            project_id=project_id,
        )
        ai_library_clear = DocumentLibraryService(self.storage).clear_group(
            group_id=normalized_group_id,
            project_id=project_id,
        )
        binding_delete = self.storage.delete_project_sync_binding(normalized_group_id)

        return {
            "group_id": normalized_group_id,
            "project_id": project_id,
            "local_path": local_path,
            "local_path_exists": bool(local_path and Path(local_path).is_dir()),
            "previous_syncthing_folder_id": previous_folder_id,
            "syncthing_folder_removed": bool(previous_folder_id and not local_only),
            "syncthing_delete_result": syncthing_delete_result,
            "local_only": bool(local_only),
            "restart_required": restart_required,
            "restart_check_error": restart_check_error,
            "project_index": project_index_clear,
            "ai_document_library": ai_library_clear,
            "binding": binding_delete,
            "real_files_deleted": False,
            "group_deleted": False,
            "messages_deleted": 0,
            "scope": "local_profile_project_unbound",
        }

    def add_member_device(
        self,
        group_id: str,
        user_id: str,
        syncthing_device_id: str,
        display_name: str = "",
    ) -> SyncDevice:
        device_id = (syncthing_device_id or "").strip()
        if not device_id:
            raise ValueError("syncthing_device_id is required")
        device = SyncDevice(
            group_id=group_id,
            user_id=(user_id or "").strip(),
            syncthing_device_id=device_id,
            display_name=(display_name or "").strip(),
            status="manual",
            metadata={"added_by": self.current_user_id},
        )
        self.storage.save_sync_device(device)

        try:
            self._client().upsert_device(device.syncthing_device_id, device.display_name or device.user_id)
        except Exception as e:
            device.metadata = {
                **(device.metadata or {}),
                "syncthing_upsert_error": str(e),
            }
            self.storage.save_sync_device(device)
            self.logger.warning(f"Saved sync device but Syncthing upsert failed: {e}")
        return device

    def poll_sync_status(self, group_id: str) -> Dict[str, Any]:
        project = self.storage.get_project_by_group(group_id)
        folder = self.storage.get_shared_folder_by_group(group_id)
        if folder is None:
            return self.get_group_sync_overview(group_id)
        if not folder.syncthing_folder_id:
            return self.get_group_sync_overview(group_id)

        client = self._client()
        recent_events: List[Dict[str, Any]] = []
        max_event_id = int(folder.last_event_id or 0)
        try:
            status_data = client.get_folder_status(folder.syncthing_folder_id)
            completion = client.get_completion(folder.syncthing_folder_id)
            events = client.get_events(since=max_event_id)
            recent_events = self._extract_file_events(folder, project, events)
            for event in events:
                try:
                    max_event_id = max(max_event_id, int(event.get("id") or 0))
                except (TypeError, ValueError):
                    pass

            folder.last_completion = float(completion.get("completion") or 0.0)
            folder.last_status = self._map_folder_status(status_data, completion)
            folder.last_error = self._extract_status_error(status_data, completion)
            folder.last_event_id = max_event_id
            folder.status = "configured"
            folder.metadata = {
                **(folder.metadata or {}),
                "status": status_data,
                "completion": completion,
                "recent_events": recent_events[-10:],
                "polled_at": time.time(),
            }
            self.storage.save_shared_folder(folder)
        except Exception as e:
            folder.last_status = "error"
            folder.last_error = str(e)
            folder.metadata = {
                **(folder.metadata or {}),
                "last_poll_error": str(e),
                "polled_at": time.time(),
            }
            self.storage.save_shared_folder(folder)

        overview = self.get_group_sync_overview(group_id)
        overview["recent_events"] = recent_events
        return overview

    def scan_group_folder(self, group_id: str) -> Dict[str, Any]:
        folder = self.storage.get_shared_folder_by_group(group_id)
        if folder is None or not folder.syncthing_folder_id:
            raise ValueError("group has no configured Syncthing folder")
        self._client().scan_folder(folder.syncthing_folder_id)
        return self.poll_sync_status(group_id)

    def build_file_attachment(self, message_id: str, metadata: Dict[str, Any]) -> FileAttachment:
        return FileAttachment(
            message_id=message_id,
            file_name=metadata.get("file_name", ""),
            size=int(metadata.get("size") or 0),
            mime_type=metadata.get("mime_type", ""),
            sha256=metadata.get("sha256", ""),
            shared_folder_id=metadata.get("shared_folder_id", ""),
            relative_path=metadata.get("relative_path", ""),
            sync_status=metadata.get("sync_status", "reserved"),
            event_type=metadata.get("event_type", ""),
            project_id=metadata.get("project_id", ""),
            origin_user_id=metadata.get("origin_user_id", ""),
            syncthing_event_id=str(metadata.get("syncthing_event_id", "")),
            metadata=metadata,
        )

    def _build_syncthing_folder_id(self, group_id: str, shared_folder_id: str) -> str:
        digest = hashlib.sha256(f"{group_id}:{shared_folder_id}".encode("utf-8")).hexdigest()[:12]
        return f"imt-{digest}"

    def _map_folder_status(self, status_data: Dict[str, Any], completion: Dict[str, Any]) -> str:
        raw_state = str(status_data.get("state") or completion.get("remoteState") or "").lower()
        if raw_state in {"scanning", "syncing", "idle", "error", "paused", "notsharing", "notSharing".lower()}:
            if raw_state == "idle" and float(completion.get("completion") or 0.0) >= 99.999:
                return "synced"
            return raw_state
        if float(completion.get("completion") or 0.0) >= 99.999:
            return "synced"
        return "syncing"

    def _extract_status_error(self, status_data: Dict[str, Any], completion: Dict[str, Any]) -> str:
        for key in ("error", "invalid", "watchError"):
            value = status_data.get(key)
            if value:
                return str(value)
        remote_state = completion.get("remoteState")
        if remote_state and remote_state not in {"valid", "unknown"}:
            return f"remoteState={remote_state}"
        return ""

    def _extract_file_events(
        self,
        folder: SharedFolder,
        project: Optional[Project],
        events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        extracted = []
        for event in events:
            if event.get("type") not in SUPPORTED_FILE_EVENT_TYPES:
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            event_folder = data.get("folder") or data.get("folderID")
            if event_folder and event_folder != folder.syncthing_folder_id:
                continue
            metadata = self._event_to_file_metadata(folder, project, event, data)
            if metadata:
                extracted.append(metadata)
        return extracted[-20:]

    def _event_to_file_metadata(
        self,
        folder: SharedFolder,
        project: Optional[Project],
        event: Dict[str, Any],
        data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        event_type = event.get("type", "")
        relative_path = str(data.get("item") or data.get("path") or data.get("name") or "")
        action = str(data.get("action") or "").lower()
        error = data.get("error")

        app_event_type = ""
        sync_status = "syncing"
        if event_type in {"LocalChangeDetected", "RemoteChangeDetected"}:
            app_event_type = self._map_action_to_event_type(action or str(data.get("type") or "updated"))
        elif event_type == "ItemFinished":
            app_event_type = "sync_error" if error else self._map_action_to_event_type(action or "updated")
            sync_status = "error" if error else "synced"
        elif event_type == "StateChanged":
            to_state = str(data.get("to") or "").lower()
            if to_state == "error":
                app_event_type = "sync_error"
                sync_status = "error"
            else:
                return None
        elif event_type == "FolderSummary":
            summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
            if int(summary.get("errors") or 0) > 0 or summary.get("error"):
                app_event_type = "sync_error"
                sync_status = "error"
                relative_path = relative_path or folder.name
            else:
                return None

        if not app_event_type:
            return None

        file_name = os.path.basename(relative_path) if relative_path else folder.name
        mime_type = mimetypes.guess_type(file_name)[0] or ""
        metadata = FILE_EVENT_METADATA_SCHEMA.copy()
        metadata.update({
            "schema": "file_event_v1",
            "event_type": app_event_type,
            "project_id": project.id if project else folder.project_id,
            "shared_folder_id": folder.id,
            "syncthing_folder_id": folder.syncthing_folder_id,
            "relative_path": relative_path,
            "file_name": file_name,
            "size": int(data.get("size") or 0),
            "mime_type": mime_type,
            "sha256": "",
            "sync_status": sync_status,
            "origin_user_id": self.current_user_id,
            "event_time": time.time(),
            "syncthing_event_id": str(event.get("id", "")),
            "syncthing_event_type": event_type,
            "syncthing_action": action,
            "error": str(error or ""),
        })
        return metadata

    def _map_action_to_event_type(self, action: str) -> str:
        normalized = (action or "").lower()
        if normalized in {"delete", "deleted", "remove", "removed"}:
            return "deleted"
        if normalized in {"create", "created", "add", "added"}:
            return "created"
        if "conflict" in normalized:
            return "conflict"
        return "updated"
