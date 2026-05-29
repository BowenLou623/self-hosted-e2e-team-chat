"""Minimal group-key encryption for phase 7 group messages."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.crypto.interface import DecryptionError
from src.crypto.direct_v2_service import ReplayProtectionError
from src.storage.sqlite_store import SQLiteStore


class GroupCryptoService:
    """AES-GCM group message encryption backed by local group_keys table."""

    ENCRYPTION_VERSION = "group_encrypted_v1"
    ALG = "AES-256-GCM"
    NONCE_SIZE = 12

    def __init__(self, storage: SQLiteStore):
        self.storage = storage

    def ensure_group_key(self, group_id: str) -> Dict[str, Any]:
        existing = self.storage.get_group_key(group_id)
        if existing:
            return existing
        return self.rotate_group_key(group_id, reason="initial")

    def rotate_group_key(self, group_id: str, reason: str = "manual") -> Dict[str, Any]:
        current = self.storage.get_group_key(group_id)
        version = int((current or {}).get("group_key_version") or 0) + 1
        raw_key = secrets.token_bytes(32)
        key_material = base64.b64encode(raw_key).decode("ascii")
        group_key_id = hashlib.sha256(f"{group_id}:{version}:{key_material}".encode("utf-8")).hexdigest()[:16]
        self.storage.save_group_key(
            group_id=group_id,
            group_key_version=version,
            group_key_id=group_key_id,
            key_material=key_material,
            status="active",
            metadata={"reason": reason, "created_at": time.time()},
        )
        return self.storage.get_group_key(group_id, version) or {
            "group_id": group_id,
            "group_key_version": version,
            "group_key_id": group_key_id,
            "key_material": key_material,
            "status": "active",
        }

    def import_group_key(
        self,
        group_id: str,
        group_key_version: int,
        group_key_id: str,
        key_material: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not group_id or not group_key_id or not key_material:
            return False
        return self.storage.save_group_key(
            group_id,
            int(group_key_version),
            group_key_id,
            key_material,
            status="active",
            metadata=metadata or {"imported_at": time.time()},
        )

    def export_key_packet(self, group_id: str) -> Dict[str, Any]:
        key = self.ensure_group_key(group_id)
        return {
            "schema": "group_key_packet_v1",
            "group_id": group_id,
            "group_key_id": key["group_key_id"],
            "group_key_version": int(key["group_key_version"]),
            "key_material": key["key_material"],
            "exported_at": time.time(),
        }

    def encrypt_payload(
        self,
        group_id: str,
        payload: Dict[str, Any],
        sender_id: str,
        sender_device_id: str,
        message_id: str,
    ) -> Dict[str, Any]:
        group_key = self.ensure_group_key(group_id)
        nonce = secrets.token_bytes(self.NONCE_SIZE)
        metadata = {
            "encryption_version": self.ENCRYPTION_VERSION,
            "alg": self.ALG,
            "group_id": group_id,
            "group_key_id": group_key["group_key_id"],
            "group_key_version": int(group_key["group_key_version"]),
            "sender_device_id": sender_device_id,
            "sequence": int(time.time() * 1_000_000),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "encryption_scope": self.ENCRYPTION_VERSION,
        }
        key = base64.b64decode(group_key["key_material"])
        aad = self._aad(message_id, sender_id, metadata)
        plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        metadata["ciphertext"] = base64.b64encode(AESGCM(key).encrypt(nonce, plaintext, aad)).decode("ascii")
        self.storage.record_message_security_seen(
            scope=f"group:{group_id}",
            direction="outgoing",
            message_id=message_id,
            sender_id=sender_id,
            sender_device_id=sender_device_id,
            key_id=str(group_key["group_key_id"]),
            sequence=int(metadata["sequence"]),
            nonce=str(metadata["nonce"]),
            metadata={"encryption_version": self.ENCRYPTION_VERSION},
        )
        return metadata

    def decrypt_payload(self, message_id: str, sender_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        self._validate_metadata(metadata)
        group_id = str(metadata["group_id"])
        group_key = self.storage.get_group_key(group_id, int(metadata["group_key_version"]))
        if not group_key:
            raise DecryptionError("missing group key")
        if group_key["group_key_id"] != metadata["group_key_id"]:
            raise DecryptionError("group key id mismatch")

        recorded, reason = self.storage.record_message_security_seen(
            scope=f"group:{group_id}",
            direction="incoming",
            message_id=message_id,
            sender_id=sender_id,
            sender_device_id=str(metadata["sender_device_id"]),
            key_id=str(metadata["group_key_id"]),
            sequence=int(metadata["sequence"]),
            nonce=str(metadata["nonce"]),
            metadata={"encryption_version": self.ENCRYPTION_VERSION},
        )
        if not recorded:
            raise ReplayProtectionError(reason)

        try:
            key = base64.b64decode(group_key["key_material"])
            nonce = base64.b64decode(str(metadata["nonce"]))
            ciphertext = base64.b64decode(str(metadata["ciphertext"]))
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, self._aad(message_id, sender_id, metadata))
            data = json.loads(plaintext.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except ReplayProtectionError:
            raise
        except Exception as exc:
            raise DecryptionError("group message decrypt failed") from exc

    def _aad(self, message_id: str, sender_id: str, metadata: Dict[str, Any]) -> bytes:
        aad = {
            "message_id": message_id,
            "sender_id": sender_id,
            "group_id": metadata.get("group_id", ""),
            "group_key_id": metadata.get("group_key_id", ""),
            "group_key_version": int(metadata.get("group_key_version") or 0),
            "sender_device_id": metadata.get("sender_device_id", ""),
            "sequence": int(metadata.get("sequence") or 0),
        }
        return json.dumps(aad, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _validate_metadata(self, metadata: Dict[str, Any]) -> None:
        required = {
            "encryption_version",
            "alg",
            "group_id",
            "group_key_id",
            "group_key_version",
            "sender_device_id",
            "sequence",
            "nonce",
            "ciphertext",
        }
        missing = [key for key in sorted(required) if key not in metadata]
        if missing:
            raise DecryptionError(f"missing group encrypted metadata: {', '.join(missing)}")
        if metadata["encryption_version"] != self.ENCRYPTION_VERSION:
            raise DecryptionError(f"unsupported group encryption version: {metadata['encryption_version']}")
        if metadata["alg"] != self.ALG:
            raise DecryptionError(f"unsupported group algorithm: {metadata['alg']}")
