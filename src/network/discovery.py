"""UDP broadcast Hub discovery for Phase 11."""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


DISCOVERY_REQUEST = "IMT_DISCOVER_V1"
DISCOVERY_RESPONSE = "IMT_HUB_V1"
DEFAULT_DISCOVERY_PORT = 8090
DEFAULT_DISCOVERY_ROUNDS = 3


@dataclass
class HubDiscoveryInfo:
    hub_id: str
    hub_name: str
    host: str
    port: int
    temp_file_port: int
    version: str
    started_at: float
    discovery_port: int = DEFAULT_DISCOVERY_PORT

    def to_payload(self, response_host: str = "") -> Dict[str, Any]:
        host = response_host or self.host
        if host in {"0.0.0.0", "::", ""}:
            host = _local_lan_ip()
        return {
            "type": DISCOVERY_RESPONSE,
            "hub_id": self.hub_id,
            "hub_name": self.hub_name,
            "host": host,
            "port": int(self.port),
            "temp_file_port": int(self.temp_file_port),
            "version": self.version,
            "started_at": float(self.started_at),
            "discovery_port": int(self.discovery_port),
        }


class HubDiscoveryService:
    """Small UDP responder hosted by HubServer."""

    def __init__(self, info: HubDiscoveryInfo):
        self.info = info
        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", int(self.info.discovery_port)))
        sock.settimeout(0.5)
        self._socket = sock
        self._running = True
        self._thread = threading.Thread(target=self._serve, name=f"hub_discovery_{self.info.discovery_port}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def _serve(self) -> None:
        assert self._socket is not None
        while self._running:
            try:
                data, addr = self._socket.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                text = data.decode("utf-8", errors="replace").strip()
                if text != DISCOVERY_REQUEST:
                    continue
                response = json.dumps(self.info.to_payload(), ensure_ascii=False, sort_keys=True).encode("utf-8")
                self._socket.sendto(response, addr)
            except OSError:
                break
            except Exception:
                continue


def discover_hubs(
    discovery_port: int = DEFAULT_DISCOVERY_PORT,
    timeout: float = 2.0,
    broadcast_addresses: Optional[List[str]] = None,
    rounds: int = DEFAULT_DISCOVERY_ROUNDS,
) -> List[Dict[str, Any]]:
    """Broadcast a discovery request and return unique Hub responses."""
    addresses = _default_broadcast_addresses(broadcast_addresses)
    started = time.time()
    found: Dict[str, Dict[str, Any]] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.2)
        payload = DISCOVERY_REQUEST.encode("utf-8")
        deadline = time.time() + max(0.2, float(timeout or 2.0))
        send_rounds = max(1, int(rounds or 1))
        for _ in range(send_rounds):
            for address in addresses:
                try:
                    sock.sendto(payload, (address, int(discovery_port)))
                except OSError:
                    continue
            if time.time() >= deadline:
                break
            time.sleep(min(0.1, max(0.0, deadline - time.time())))

        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                item = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(item, dict) or item.get("type") != DISCOVERY_RESPONSE:
                continue
            payload_host = str(item.get("host") or "").strip()
            source_host = str(addr[0] or "").strip()
            host = _choose_response_host(payload_host, source_host)
            item["payload_host"] = payload_host
            item["source_host"] = source_host
            item["host"] = host
            item["address"] = f"{host}:{int(item.get('port') or 0)}"
            item["source_address"] = f"{source_host}:{int(item.get('port') or 0)}" if source_host else item["address"]
            item["response_ms"] = int((time.time() - started) * 1000)
            key = str(item.get("hub_id") or item["address"] or uuid.uuid4().hex)
            found[key] = item
    finally:
        sock.close()
    return sorted(found.values(), key=lambda item: (int(item.get("response_ms") or 0), item.get("hub_name") or "", item.get("address") or ""))


def _default_broadcast_addresses(addresses: Optional[List[str]] = None) -> List[str]:
    candidates: List[str] = []
    if addresses:
        candidates.extend(addresses)
    else:
        candidates.append("255.255.255.255")
        lan_ip = _local_lan_ip()
        parts = lan_ip.split(".")
        if len(parts) == 4 and parts[0] != "127":
            candidates.append(".".join(parts[:3] + ["255"]))
    result: List[str] = []
    seen = set()
    for address in candidates:
        normalized = str(address or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result or ["255.255.255.255"]


def _choose_response_host(payload_host: str, source_host: str) -> str:
    payload = str(payload_host or "").strip()
    source = str(source_host or "").strip()
    if source and not source.startswith("127."):
        return source
    if payload.lower() in {"", "0.0.0.0", "::", "127.0.0.1", "localhost"}:
        return source or payload
    return payload


def _local_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
