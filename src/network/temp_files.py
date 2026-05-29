"""Hub-side encrypted temporary file storage."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


DEFAULT_TEMP_FILE_TTL_SECONDS = 30 * 60
DEFAULT_TEMP_FILE_MAX_BYTES = 25 * 1024 * 1024


class TempFileError(Exception):
    """Temp file operation failed."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class TempFileStore:
    """Stores encrypted temp files and JSON manifests on Hub disk."""

    def __init__(
        self,
        root_dir: str,
        ttl_seconds: int = DEFAULT_TEMP_FILE_TTL_SECONDS,
        max_bytes: int = DEFAULT_TEMP_FILE_MAX_BYTES,
    ):
        self.root_dir = Path(root_dir)
        self.ttl_seconds = max(60, int(ttl_seconds or DEFAULT_TEMP_FILE_TTL_SECONDS))
        self.max_bytes = max(1024, int(max_bytes or DEFAULT_TEMP_FILE_MAX_BYTES))
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(
        self,
        file_id: str,
        owner_user_id: str,
        ciphertext: bytes,
        ciphertext_sha256: str,
        access_token: str,
        expires_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        file_id = self._normalize_file_id(file_id)
        owner_user_id = str(owner_user_id or "").strip()
        access_token = str(access_token or "").strip()
        expected_hash = str(ciphertext_sha256 or "").strip().lower()
        if not owner_user_id:
            raise TempFileError("missing_owner", "owner_user_id is required")
        if not access_token:
            raise TempFileError("missing_token", "access_token is required")
        if not ciphertext:
            raise TempFileError("empty_file", "ciphertext body is empty")
        if len(ciphertext) > self.max_bytes:
            raise TempFileError("file_too_large", "ciphertext exceeds Hub temp file limit", 413)
        actual_hash = hashlib.sha256(ciphertext).hexdigest()
        if expected_hash and actual_hash != expected_hash:
            raise TempFileError("hash_mismatch", "ciphertext_sha256 mismatch")

        now = time.time()
        max_expires_at = now + self.ttl_seconds
        if expires_at is None or expires_at <= now or expires_at > max_expires_at + 5:
            expires_at = max_expires_at

        manifest = {
            "file_id": file_id,
            "owner_user_id": owner_user_id,
            "created_at": now,
            "expires_at": float(expires_at),
            "ciphertext_size": len(ciphertext),
            "ciphertext_sha256": actual_hash,
            "token_hash": self._token_hash(access_token),
            "status": "available",
        }
        with self._lock:
            bin_path = self._bin_path(file_id)
            manifest_path = self._manifest_path(file_id)
            if bin_path.exists() or manifest_path.exists():
                raise TempFileError("file_exists", "temp file already exists", 409)
            part_path = bin_path.with_suffix(".bin.part")
            part_path.write_bytes(ciphertext)
            part_path.replace(bin_path)
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        return self.public_manifest(manifest)

    def read(self, file_id: str, access_token: str) -> Tuple[bytes, Dict[str, Any]]:
        file_id = self._normalize_file_id(file_id)
        access_token = str(access_token or "").strip()
        with self._lock:
            manifest = self._load_manifest(file_id)
            if manifest is None:
                raise TempFileError("not_found", "temp file not found", 404)
            if self._is_expired(manifest):
                self._delete_locked(file_id)
                raise TempFileError("expired", "temp file expired", 410)
            if not secrets.compare_digest(str(manifest.get("token_hash") or ""), self._token_hash(access_token)):
                raise TempFileError("forbidden", "invalid access token", 403)
            bin_path = self._bin_path(file_id)
            if not bin_path.exists():
                self._delete_locked(file_id)
                raise TempFileError("not_found", "ciphertext missing", 404)
            data = bin_path.read_bytes()
            if hashlib.sha256(data).hexdigest() != manifest.get("ciphertext_sha256"):
                raise TempFileError("hash_mismatch", "stored ciphertext hash mismatch", 500)
            return data, self.public_manifest(manifest)

    def cleanup_expired(self) -> int:
        removed = 0
        with self._lock:
            for manifest_path in self.root_dir.glob("tmp_*.json"):
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    manifest_path.unlink(missing_ok=True)
                    removed += 1
                    continue
                file_id = str(manifest.get("file_id") or manifest_path.stem)
                if self._is_expired(manifest) or not self._bin_path(file_id).exists():
                    self._delete_locked(file_id)
                    removed += 1
        return removed

    def status(self) -> Dict[str, Any]:
        self.cleanup_expired()
        manifests = list(self.root_dir.glob("tmp_*.json"))
        return {
            "status": "ok",
            "temp_file_dir": str(self.root_dir),
            "ttl_seconds": self.ttl_seconds,
            "max_bytes": self.max_bytes,
            "file_count": len(manifests),
        }

    def public_manifest(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in manifest.items()
            if key != "token_hash"
        }

    def _load_manifest(self, file_id: str) -> Optional[Dict[str, Any]]:
        path = self._manifest_path(file_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise TempFileError("invalid_manifest", str(exc), 500) from exc
        return data if isinstance(data, dict) else None

    def _delete_locked(self, file_id: str) -> None:
        self._bin_path(file_id).unlink(missing_ok=True)
        self._manifest_path(file_id).unlink(missing_ok=True)
        self._bin_path(file_id).with_suffix(".bin.part").unlink(missing_ok=True)

    def _is_expired(self, manifest: Dict[str, Any]) -> bool:
        return float(manifest.get("expires_at") or 0) <= time.time()

    def _bin_path(self, file_id: str) -> Path:
        return self.root_dir / f"{file_id}.bin"

    def _manifest_path(self, file_id: str) -> Path:
        return self.root_dir / f"{file_id}.json"

    def _normalize_file_id(self, file_id: str) -> str:
        value = str(file_id or "").strip()
        if not value:
            value = "tmp_" + secrets.token_urlsafe(18).replace("-", "_")
        if not value.startswith("tmp_"):
            raise TempFileError("invalid_file_id", "file_id must start with tmp_")
        if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in value):
            raise TempFileError("invalid_file_id", "file_id contains invalid characters")
        return value

    def _token_hash(self, access_token: str) -> str:
        return hashlib.sha256(str(access_token or "").encode("utf-8")).hexdigest()


class TempFileHTTPServer:
    """Small HTTP sidecar hosted inside HubServer."""

    def __init__(self, host: str, port: int, store: TempFileStore):
        self.host = host
        self.port = int(port)
        self.store = store
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        handler = self._handler_class(self.store)
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"temp_file_http_{self.port}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _handler_class(self, store: TempFileStore):
        class Handler(BaseHTTPRequestHandler):
            server_version = "IMTTempFile/1.0"

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/status":
                    self._write_json(200, store.status())
                    return
                file_id = self._file_id_from_path(parsed.path)
                if not file_id:
                    self._write_json(404, {"ok": False, "error": "not_found"})
                    return
                query = urllib.parse.parse_qs(parsed.query)
                token = query.get("token", [""])[0]
                try:
                    data, manifest = store.read(file_id, token)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("X-Temp-File-Expires-At", str(manifest.get("expires_at", "")))
                    self.end_headers()
                    self.wfile.write(data)
                except TempFileError as exc:
                    self._write_json(exc.http_status, {"ok": False, "error": exc.code, "message": str(exc)})

            def do_PUT(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                file_id = self._file_id_from_path(parsed.path)
                if not file_id:
                    self._write_json(404, {"ok": False, "error": "not_found"})
                    return
                query = urllib.parse.parse_qs(parsed.query)
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    content_length = 0
                if content_length <= 0:
                    self._write_json(400, {"ok": False, "error": "empty_file"})
                    return
                if content_length > store.max_bytes:
                    self._write_json(413, {"ok": False, "error": "file_too_large"})
                    return
                ciphertext = self.rfile.read(content_length)
                try:
                    manifest = store.create(
                        file_id=file_id,
                        owner_user_id=query.get("owner_user_id", [""])[0],
                        ciphertext=ciphertext,
                        ciphertext_sha256=query.get("ciphertext_sha256", [""])[0],
                        access_token=query.get("access_token", [""])[0],
                        expires_at=float(query.get("expires_at", ["0"])[0] or 0),
                    )
                    self._write_json(201, {"ok": True, "manifest": manifest})
                except TempFileError as exc:
                    self._write_json(exc.http_status, {"ok": False, "error": exc.code, "message": str(exc)})

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _file_id_from_path(self, path: str) -> str:
                prefix = "/temp-files/"
                if not path.startswith(prefix):
                    return ""
                return urllib.parse.unquote(path[len(prefix):]).strip("/")

            def _write_json(self, status: int, payload: Dict[str, Any]) -> None:
                raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        return Handler
