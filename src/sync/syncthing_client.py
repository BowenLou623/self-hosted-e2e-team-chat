"""Small REST client for Syncthing's GUI API."""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from src.sync.settings import SyncthingSettings


class SyncthingAPIError(Exception):
    """Raised when Syncthing returns an HTTP or connection error."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class SyncthingClient:
    """Thin REST adapter; it does not contain app business rules."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8384",
        api_key: str = "",
        timeout_seconds: float = 2.0,
    ):
        self.base_url = (base_url or "http://127.0.0.1:8384").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout_seconds = float(timeout_seconds or 2.0)

    @classmethod
    def from_settings(cls, settings: SyncthingSettings) -> "SyncthingClient":
        return cls(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout_seconds=settings.timeout_seconds,
        )

    def _url(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{normalized_path}"
        if params:
            clean_params = {key: value for key, value in params.items() if value is not None}
            if clean_params:
                url = f"{url}?{urllib.parse.urlencode(clean_params)}"
        return url

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        headers = {"Accept": "application/json"}
        body = None
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            self._url(path, params=params),
            data=body,
            method=method.upper(),
            headers=headers,
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                if not raw:
                    return {}
                text = raw.decode("utf-8")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"raw": text}
        except urllib.error.HTTPError as e:
            try:
                details = e.read().decode("utf-8")
            except Exception:
                details = e.reason or str(e)
            raise SyncthingAPIError(f"Syncthing HTTP {e.code}: {details}", e.code) from e
        except urllib.error.URLError as e:
            raise SyncthingAPIError(f"Syncthing connection failed: {e.reason}") from e
        except TimeoutError as e:
            raise SyncthingAPIError("Syncthing request timed out") from e

    def ping(self) -> Dict[str, Any]:
        return self._request("GET", "/rest/system/ping")

    def get_status(self) -> Dict[str, Any]:
        return self._request("GET", "/rest/system/status")

    def get_version(self) -> Dict[str, Any]:
        return self._request("GET", "/rest/system/version")

    def get_local_device_id(self) -> str:
        status = self.get_status()
        return str(status.get("myID") or status.get("myId") or "")

    def list_folders(self) -> List[Dict[str, Any]]:
        data = self._request("GET", "/rest/config/folders")
        return data if isinstance(data, list) else []

    def list_devices(self) -> List[Dict[str, Any]]:
        data = self._request("GET", "/rest/config/devices")
        return data if isinstance(data, list) else []

    def get_default_folder_config(self) -> Dict[str, Any]:
        data = self._request("GET", "/rest/config/defaults/folder")
        return data if isinstance(data, dict) else {}

    def get_default_device_config(self) -> Dict[str, Any]:
        data = self._request("GET", "/rest/config/defaults/device")
        return data if isinstance(data, dict) else {}

    def upsert_device(self, device_id: str, name: str = "") -> Dict[str, Any]:
        device_id = (device_id or "").strip()
        if not device_id:
            raise ValueError("device_id is required")

        device = self.get_default_device_config()
        device["deviceID"] = device_id
        if name:
            device["name"] = name
        elif not device.get("name"):
            device["name"] = device_id[:12]
        self._request("POST", "/rest/config/devices", payload=device)
        return device

    def upsert_folder(
        self,
        folder_id: str,
        path: str,
        label: str = "",
        device_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        folder_id = (folder_id or "").strip()
        path = (path or "").strip()
        if not folder_id:
            raise ValueError("folder_id is required")
        if not path:
            raise ValueError("path is required")

        folder = self.get_default_folder_config()
        folder["id"] = folder_id
        folder["label"] = label or folder_id
        folder["path"] = path
        folder["type"] = folder.get("type") or "sendreceive"
        folder["fsWatcherEnabled"] = folder.get("fsWatcherEnabled", True)

        devices = []
        seen = set()
        for device_id in device_ids or []:
            normalized = (device_id or "").strip()
            if normalized and normalized not in seen:
                devices.append({"deviceID": normalized})
                seen.add(normalized)
        folder["devices"] = devices

        self._request("POST", "/rest/config/folders", payload=folder)
        return folder

    def delete_folder(self, folder_id: str) -> Dict[str, Any]:
        """Remove a Syncthing folder config. Missing folders are idempotent success."""
        folder_id = (folder_id or "").strip()
        if not folder_id:
            raise ValueError("folder_id is required")
        quoted_folder_id = urllib.parse.quote(folder_id, safe="")
        try:
            self._request("DELETE", f"/rest/config/folders/{quoted_folder_id}")
            return {"deleted": True, "missing": False, "folder_id": folder_id}
        except SyncthingAPIError as e:
            if e.status_code == 404:
                return {"deleted": False, "missing": True, "folder_id": folder_id}
            raise

    def get_restart_required(self) -> bool:
        data = self._request("GET", "/rest/config/restart-required")
        if isinstance(data, dict):
            return bool(data.get("requiresRestart") or data.get("restartRequired"))
        return False

    def get_folder_status(self, folder_id: str) -> Dict[str, Any]:
        return self._request("GET", "/rest/db/status", params={"folder": folder_id})

    def get_completion(self, folder_id: Optional[str] = None, device_id: Optional[str] = None) -> Dict[str, Any]:
        params = {}
        if folder_id:
            params["folder"] = folder_id
        if device_id:
            params["device"] = device_id
        return self._request("GET", "/rest/db/completion", params=params)

    def get_events(self, since: int = 0, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"since": int(since or 0)}
        if limit is not None:
            params["limit"] = int(limit)
        data = self._request("GET", "/rest/events", params=params)
        return data if isinstance(data, list) else []

    def scan_folder(self, folder_id: str) -> Dict[str, Any]:
        return self._request("POST", "/rest/db/scan", params={"folder": folder_id})
