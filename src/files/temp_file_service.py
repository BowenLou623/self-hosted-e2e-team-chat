"""Client-side encrypted temporary file upload/download."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


TEMP_FILE_SCHEMA = "temp_file_v1"
TEMP_FILE_ALG = "AES-256-GCM"
DEFAULT_TEMP_FILE_TTL_SECONDS = 30 * 60
DEFAULT_TEMP_FILE_MAX_BYTES = 25 * 1024 * 1024


class TempFileServiceError(Exception):
    """Temporary file operation failed with a UI-friendly reason code."""

    def __init__(self, message: str, reason: str = "unknown"):
        super().__init__(message)
        self.message = message
        self.reason = reason

    def __str__(self) -> str:
        reason_labels = {
            "hub_offline": "Hub 不在线",
            "expired": "文件已过期",
            "decrypt_failed": "解密失败",
            "network_error": "网络错误",
        }
        label = reason_labels.get(self.reason)
        if label and label not in self.message:
            return f"{label}: {self.message}"
        return self.message


@dataclass
class PreparedTempFile:
    metadata: Dict[str, Any]
    ciphertext: bytes


class TempFileService:
    """Encrypts files locally and stores only ciphertext on the Hub."""

    def __init__(
        self,
        hub_address: str,
        data_dir: str,
        temp_file_base_url: str = "",
        ttl_seconds: int = DEFAULT_TEMP_FILE_TTL_SECONDS,
        max_bytes: int = DEFAULT_TEMP_FILE_MAX_BYTES,
        timeout_seconds: float = 20.0,
    ):
        self.hub_address = (hub_address or "127.0.0.1:8080").strip()
        self.data_dir = Path(data_dir or "data")
        self.base_url = (temp_file_base_url or self._derive_base_url(self.hub_address)).rstrip("/")
        self.ttl_seconds = max(60, int(ttl_seconds or DEFAULT_TEMP_FILE_TTL_SECONDS))
        self.max_bytes = max(1024, int(max_bytes or DEFAULT_TEMP_FILE_MAX_BYTES))
        self.timeout_seconds = float(timeout_seconds or 20.0)

    def prepare(self, file_path: str, message_id: str, sender_id: str, scope: str, conversation_id: str = "", group_id: str = "") -> PreparedTempFile:
        path = Path(file_path).expanduser().resolve(strict=False)
        if path.is_symlink() or not path.exists() or not path.is_file():
            raise TempFileServiceError("请选择一个普通文件", "invalid_file")
        size = path.stat().st_size
        if size <= 0:
            raise TempFileServiceError("不能发送空文件", "invalid_file")
        if size > self.max_bytes:
            raise TempFileServiceError(f"文件超过临时发送大小上限: {self.max_bytes} bytes", "invalid_file")

        plaintext = path.read_bytes()
        created_at = time.time()
        expires_at = created_at + self.ttl_seconds
        file_id = "tmp_" + secrets.token_urlsafe(18).replace("-", "_")
        access_token = secrets.token_urlsafe(32)
        file_key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        plaintext_sha256 = hashlib.sha256(plaintext).hexdigest()
        aad = self._aad(file_id, message_id, sender_id, expires_at, plaintext_sha256)
        ciphertext = AESGCM(file_key).encrypt(nonce, plaintext, aad)
        ciphertext_sha256 = hashlib.sha256(ciphertext).hexdigest()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        metadata = {
            "schema": TEMP_FILE_SCHEMA,
            "file_id": file_id,
            "message_id": message_id,
            "scope": scope,
            "conversation_id": conversation_id,
            "group_id": group_id,
            "file_name": path.name,
            "size": size,
            "mime_type": mime_type,
            "plaintext_sha256": plaintext_sha256,
            "ciphertext_sha256": ciphertext_sha256,
            "ciphertext_size": len(ciphertext),
            "alg": TEMP_FILE_ALG,
            "file_key_b64": base64.b64encode(file_key).decode("ascii"),
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "access_token": access_token,
            "hub_file_url": f"{self.base_url}/temp-files/{urllib.parse.quote(file_id)}",
            "created_at": created_at,
            "expires_at": expires_at,
            "crypto_expires_at": expires_at,
            "ttl_seconds": self.ttl_seconds,
            "sender_user_id": sender_id,
            "sync_status": "temporary_available",
        }
        return PreparedTempFile(metadata=metadata, ciphertext=ciphertext)

    def upload_prepared(self, prepared: PreparedTempFile, owner_user_id: str) -> Dict[str, Any]:
        metadata = prepared.metadata
        file_id = str(metadata.get("file_id") or "")
        params = {
            "owner_user_id": owner_user_id,
            "access_token": str(metadata.get("access_token") or ""),
            "ciphertext_sha256": str(metadata.get("ciphertext_sha256") or ""),
            "expires_at": str(metadata.get("expires_at") or ""),
        }
        url = f"{self.base_url}/temp-files/{urllib.parse.quote(file_id)}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            data=prepared.ciphertext,
            method="PUT",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(prepared.ciphertext)),
            },
        )
        try:
            with self._urlopen_no_proxy(request) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise TempFileServiceError(
                self._http_error_message("上传", exc.code, details),
                self._http_error_reason(exc.code, details),
            ) from exc
        except urllib.error.URLError as exc:
            message, reason = self._connection_error_message(exc.reason)
            raise TempFileServiceError(message, reason) from exc
        except json.JSONDecodeError as exc:
            raise TempFileServiceError("临时文件服务返回无效 JSON", "network_error") from exc
        manifest = payload.get("manifest") if isinstance(payload, dict) else None
        if not isinstance(manifest, dict):
            raise TempFileServiceError(f"临时文件上传响应无效: {payload}", "network_error")
        metadata["expires_at"] = float(manifest.get("expires_at") or metadata.get("expires_at") or 0)
        return metadata

    def encrypt_and_upload(
        self,
        file_path: str,
        message_id: str,
        sender_id: str,
        scope: str,
        conversation_id: str = "",
        group_id: str = "",
    ) -> Dict[str, Any]:
        prepared = self.prepare(
            file_path=file_path,
            message_id=message_id,
            sender_id=sender_id,
            scope=scope,
            conversation_id=conversation_id,
            group_id=group_id,
        )
        return self.upload_prepared(prepared, owner_user_id=sender_id)

    def download_and_decrypt(self, metadata: Dict[str, Any], output_dir: Optional[str] = None) -> str:
        if not isinstance(metadata, dict) or metadata.get("schema") != TEMP_FILE_SCHEMA:
            raise TempFileServiceError("不是临时文件消息", "invalid_metadata")
        if float(metadata.get("expires_at") or 0) <= time.time():
            raise TempFileServiceError("临时文件已过期", "expired")
        url = str(metadata.get("hub_file_url") or "").strip()
        token = str(metadata.get("access_token") or "").strip()
        if not url or not token:
            raise TempFileServiceError("临时文件 metadata 缺少下载信息", "invalid_metadata")
        separator = "&" if "?" in url else "?"
        request = urllib.request.Request(f"{url}{separator}{urllib.parse.urlencode({'token': token})}", method="GET")
        try:
            with self._urlopen_no_proxy(request) as response:
                ciphertext = response.read()
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise TempFileServiceError(
                self._http_error_message("下载", exc.code, details),
                self._http_error_reason(exc.code, details),
            ) from exc
        except urllib.error.URLError as exc:
            message, reason = self._connection_error_message(exc.reason)
            raise TempFileServiceError(message, reason) from exc
        expected_cipher_hash = str(metadata.get("ciphertext_sha256") or "")
        if hashlib.sha256(ciphertext).hexdigest() != expected_cipher_hash:
            raise TempFileServiceError("临时文件密文校验失败", "decrypt_failed")

        try:
            key = base64.b64decode(str(metadata.get("file_key_b64") or ""))
            nonce = base64.b64decode(str(metadata.get("nonce_b64") or ""))
            aad = self._aad(
                str(metadata.get("file_id") or ""),
                str(metadata.get("message_id") or ""),
                str(metadata.get("sender_user_id") or ""),
                float(metadata.get("crypto_expires_at") or metadata.get("expires_at") or 0),
                str(metadata.get("plaintext_sha256") or ""),
            )
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        except Exception as exc:
            raise TempFileServiceError("临时文件解密失败", "decrypt_failed") from exc
        if hashlib.sha256(plaintext).hexdigest() != str(metadata.get("plaintext_sha256") or ""):
            raise TempFileServiceError("临时文件明文校验失败", "decrypt_failed")

        target_dir = Path(output_dir).expanduser() if output_dir else self.data_dir / "temp_downloads"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = self._unique_path(target_dir, str(metadata.get("file_name") or "download.bin"))
        target_path.write_bytes(plaintext)
        return str(target_path)

    def _aad(self, file_id: str, message_id: str, sender_id: str, expires_at: float, plaintext_sha256: str) -> bytes:
        data = {
            "file_id": file_id,
            "message_id": message_id,
            "sender_id": sender_id,
            "expires_at": round(float(expires_at or 0), 3),
            "plaintext_sha256": plaintext_sha256,
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _derive_base_url(self, hub_address: str) -> str:
        if "://" in hub_address:
            parsed = urllib.parse.urlparse(hub_address)
            host = parsed.hostname or "127.0.0.1"
            port = (parsed.port or 8080) + 1
        else:
            host, _, port_text = hub_address.partition(":")
            host = host or "127.0.0.1"
            try:
                port = int(port_text or "8080") + 1
            except ValueError:
                port = 8081
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"http://{host}:{port}"

    def _urlopen_no_proxy(self, request: urllib.request.Request):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=self.timeout_seconds)

    def _connection_error_message(self, reason: Any) -> tuple[str, str]:
        reason_text = str(reason)
        if "Connection refused" in reason_text or "Errno 61" in reason_text:
            return (
                f"Hub 临时文件服务未启动: {self.base_url}。"
                "请重启 Phase 8 Hub；临时文件服务默认监听 Hub 端口 + 1。",
                "hub_offline",
            )
        return f"临时文件服务连接失败: {reason_text}", "network_error"

    def _http_error_message(self, action: str, code: int, details: str) -> str:
        normalized = details.lower()
        if code == 410 or "expired" in normalized:
            return f"临时文件{action}失败: 文件已过期"
        if code == 503 and not details.strip():
            return (
                f"临时文件{action}失败 HTTP 503。"
                f"已绕过系统代理访问 {self.base_url}；请确认该端口运行的是 Phase 8 Hub 临时文件服务。"
            )
        return f"临时文件{action}失败 HTTP {code}: {details}"

    def _http_error_reason(self, code: int, details: str) -> str:
        normalized = details.lower()
        if code == 410 or "expired" in normalized:
            return "expired"
        if code in {502, 503, 504}:
            return "hub_offline"
        return "network_error"

    def _unique_path(self, directory: Path, file_name: str) -> Path:
        safe_name = Path(file_name).name or "download.bin"
        candidate = directory / safe_name
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        for index in range(1, 1000):
            next_candidate = directory / f"{stem}-{index}{suffix}"
            if not next_candidate.exists():
                return next_candidate
        raise TempFileServiceError("无法生成本地下载文件名", "invalid_file")
