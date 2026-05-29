"""Hub runtime marker helpers for local-only admin operations."""

from __future__ import annotations

import json
import os
import platform
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


ADMIN_USERNAME = "admin"
RUNTIME_MARKER = "hub_runtime.json"


def local_machine_id() -> str:
    """Return a stable-enough local machine identifier without external deps."""
    candidates = [
        Path("/etc/machine-id"),
        Path("/var/lib/dbus/machine-id"),
    ]
    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return f"{socket.gethostname()}-{uuid.getnode():012x}"


def write_runtime_marker(
    hub_dir: str | Path,
    *,
    hub_id: str,
    host: str,
    port: int,
    temp_file_port: int = 0,
    discovery_port: int = 0,
) -> Dict[str, Any]:
    hub_path = Path(hub_dir).expanduser()
    hub_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "hub_id": hub_id,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "machine_id": local_machine_id(),
        "platform": platform.platform(),
        "host": host,
        "port": int(port),
        "temp_file_port": int(temp_file_port or 0),
        "discovery_port": int(discovery_port or 0),
        "started_at": time.time(),
        "updated_at": time.time(),
        "status": "running",
    }
    marker_path(hub_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def mark_runtime_stopped(hub_dir: str | Path) -> None:
    path = marker_path(hub_dir)
    marker = read_runtime_marker(hub_dir) or {}
    if not marker:
        return
    marker["status"] = "stopped"
    marker["stopped_at"] = time.time()
    marker["updated_at"] = time.time()
    try:
        path.write_text(json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def read_runtime_marker(hub_dir: str | Path) -> Optional[Dict[str, Any]]:
    path = marker_path(hub_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def runtime_status(hub_dir: str | Path) -> Dict[str, Any]:
    marker = read_runtime_marker(hub_dir)
    denied_reason = ""
    local_running = False
    if not marker:
        denied_reason = "Hub is not running on this host"
    else:
        marker_machine = str(marker.get("machine_id") or "")
        pid = int(marker.get("pid") or 0)
        status = str(marker.get("status") or "")
        if marker_machine != local_machine_id():
            denied_reason = "Hub runtime marker belongs to another host"
        elif status != "running":
            denied_reason = "Hub runtime marker is not running"
        elif not is_pid_alive(pid):
            denied_reason = "Hub process is not alive"
        else:
            local_running = True

    return {
        "local_hub_running": local_running,
        "admin_available": local_running,
        "admin_username": ADMIN_USERNAME,
        "hub_runtime": marker or {},
        "denied_reason": "" if local_running else denied_reason,
    }


def require_local_hub_running(hub_dir: str | Path) -> Dict[str, Any]:
    status = runtime_status(hub_dir)
    if not status["local_hub_running"]:
        raise ValueError(status["denied_reason"] or "Hub is not running on this host")
    return status


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def marker_path(hub_dir: str | Path) -> Path:
    return Path(hub_dir).expanduser() / RUNTIME_MARKER
