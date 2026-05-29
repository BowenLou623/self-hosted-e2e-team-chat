"""Direct message encryption v2 with device identity keys."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from src.crypto.device_identity import DeviceIdentityStore
from src.crypto.interface import DecryptionError
from src.storage.sqlite_store import SQLiteStore


class ReplayProtectionError(DecryptionError):
    """Raised when a message was already seen or reuses a nonce."""


class DirectV2CryptoService:
    """AES-GCM direct encryption keyed by X25519 device identity exchange."""

    ENCRYPTION_VERSION = "direct_encrypted_v2"
    ALG = "AES-256-GCM"
    NONCE_SIZE = 12

    def __init__(self, identity_store: DeviceIdentityStore, storage: SQLiteStore, local_user_id: str):
        self.identity_store = identity_store
        self.storage = storage
        self.local_user_id = (local_user_id or "").strip()

    @property
    def local_identity(self):
        return self.identity_store.load_or_create()

    def local_public_profile(self) -> Dict[str, str]:
        return self.local_identity.public_bundle()

    def encrypt_message(
        self,
        plaintext: str,
        sender_id: str,
        recipient_id: str,
        recipient_device_id: str,
        recipient_public_key: str,
        message_id: str,
        key_version: Optional[int] = None,
        scope: str = "direct",
    ) -> Dict[str, Any]:
        key_version = int(key_version or self.storage.get_active_crypto_session_version(recipient_id, recipient_device_id))
        key, key_id = self._derive_key(
            peer_user_id=recipient_id,
            peer_device_id=recipient_device_id,
            peer_public_key=recipient_public_key,
            key_version=key_version,
        )
        self.storage.save_crypto_session(
            recipient_id,
            recipient_device_id,
            key_version,
            key_id,
            recipient_public_key,
            status="active",
        )
        sequence = self.storage.next_crypto_send_sequence(recipient_id, recipient_device_id, key_version)
        nonce = secrets.token_bytes(self.NONCE_SIZE)
        metadata = {
            "encryption_version": self.ENCRYPTION_VERSION,
            "sender_device_id": self.local_identity.local_device_id,
            "recipient_device_id": recipient_device_id,
            "key_id": key_id,
            "key_version": key_version,
            "sequence": sequence,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "alg": self.ALG,
            "scope": scope,
        }
        aad = self._aad(
            message_id=message_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            metadata=metadata,
        )
        ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
        metadata["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
        self.storage.record_message_security_seen(
            scope=scope,
            direction="outgoing",
            message_id=message_id,
            sender_id=sender_id,
            sender_device_id=self.local_identity.local_device_id,
            key_id=key_id,
            sequence=sequence,
            nonce=metadata["nonce"],
            metadata={"recipient_id": recipient_id},
        )
        return metadata

    def decrypt_message(
        self,
        metadata: Dict[str, Any],
        message_id: str,
        sender_id: str,
        recipient_id: str,
        sender_public_key: str,
        scope: str = "direct",
    ) -> str:
        self._validate_metadata(metadata)
        sender_device_id = str(metadata["sender_device_id"])
        key_version = int(metadata["key_version"])
        key, key_id = self._derive_key(
            peer_user_id=sender_id,
            peer_device_id=sender_device_id,
            peer_public_key=sender_public_key,
            key_version=key_version,
        )
        if key_id != metadata["key_id"]:
            raise DecryptionError("key_id mismatch")

        recorded, reason = self.storage.record_message_security_seen(
            scope=scope,
            direction="incoming",
            message_id=message_id,
            sender_id=sender_id,
            sender_device_id=sender_device_id,
            key_id=key_id,
            sequence=int(metadata["sequence"]),
            nonce=str(metadata["nonce"]),
            metadata={"recipient_id": recipient_id},
        )
        if not recorded:
            raise ReplayProtectionError(reason)

        try:
            nonce = base64.b64decode(str(metadata["nonce"]))
            ciphertext = base64.b64decode(str(metadata["ciphertext"]))
        except Exception as exc:
            raise DecryptionError(f"invalid base64 metadata: {exc}") from exc
        if len(nonce) != self.NONCE_SIZE:
            raise DecryptionError("invalid nonce length")

        aad = self._aad(
            message_id=message_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            metadata=metadata,
        )
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
            self.storage.save_crypto_session(
                sender_id,
                sender_device_id,
                key_version,
                key_id,
                sender_public_key,
                status="active",
            )
            return plaintext.decode("utf-8")
        except Exception as exc:
            raise DecryptionError("direct v2 decrypt failed") from exc

    def rotate_session(self, peer_user_id: str, peer_device_id: str) -> int:
        return self.storage.rotate_crypto_session(peer_user_id, peer_device_id)

    def _derive_key(
        self,
        peer_user_id: str,
        peer_device_id: str,
        peer_public_key: str,
        key_version: int,
    ) -> tuple[bytes, str]:
        private = self.identity_store.private_key_obj()
        peer_public = self.identity_store.public_key_obj(peer_public_key)
        shared = private.exchange(peer_public)
        local = self.local_identity
        users = sorted([self.local_user_id, peer_user_id])
        devices = sorted([local.local_device_id, peer_device_id])
        info_text = (
            f"imt:direct:v2:{users[0]}:{users[1]}:"
            f"{devices[0]}:{devices[1]}:v{int(key_version)}"
        )
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=info_text.encode("utf-8"),
        ).derive(shared)
        key_id = hashlib.sha256(info_text.encode("utf-8") + shared).hexdigest()[:16]
        return key, key_id

    def _aad(self, message_id: str, sender_id: str, recipient_id: str, metadata: Dict[str, Any]) -> bytes:
        aad = {
            "message_id": message_id,
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "sender_device_id": metadata.get("sender_device_id", ""),
            "recipient_device_id": metadata.get("recipient_device_id", ""),
            "key_id": metadata.get("key_id", ""),
            "key_version": int(metadata.get("key_version") or 0),
            "sequence": int(metadata.get("sequence") or 0),
            "scope": metadata.get("scope", "direct"),
        }
        return json.dumps(aad, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _validate_metadata(self, metadata: Dict[str, Any]) -> None:
        required = {
            "encryption_version",
            "sender_device_id",
            "recipient_device_id",
            "key_id",
            "key_version",
            "sequence",
            "nonce",
            "ciphertext",
            "alg",
        }
        missing = [key for key in sorted(required) if key not in metadata]
        if missing:
            raise DecryptionError(f"missing direct v2 metadata: {', '.join(missing)}")
        if metadata["encryption_version"] != self.ENCRYPTION_VERSION:
            raise DecryptionError(f"unsupported direct encryption version: {metadata['encryption_version']}")
        if metadata.get("recipient_device_id") != self.local_identity.local_device_id:
            raise DecryptionError("recipient_device_id mismatch")
        if metadata.get("alg") != self.ALG:
            raise DecryptionError(f"unsupported direct algorithm: {metadata.get('alg')}")
