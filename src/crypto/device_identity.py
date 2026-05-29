"""Profile-local device identity keys for phase 7 encryption."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519


@dataclass
class DeviceIdentityKey:
    """A local X25519 device identity."""

    local_device_id: str
    public_key: str
    private_key: str
    fingerprint: str
    created_at: float
    device_name: str = ""

    def public_bundle(self) -> Dict[str, str]:
        return {
            "device_id": self.local_device_id,
            "device_name": self.device_name,
            "device_public_key": self.public_key,
            "device_fingerprint": self.fingerprint,
        }


class DeviceIdentityStore:
    """Load or create a profile-local device identity keypair."""

    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir or "data/config")
        self.path = self.config_dir / "device_identity.json"
        self._identity: Optional[DeviceIdentityKey] = None

    def load_or_create(self) -> DeviceIdentityKey:
        if self._identity is not None:
            return self._identity
        loaded = self._load()
        if loaded is not None:
            self._identity = loaded
            return loaded
        self._identity = self._create()
        return self._identity

    def _load(self) -> Optional[DeviceIdentityKey]:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            identity = DeviceIdentityKey(
                local_device_id=str(data.get("local_device_id") or ""),
                public_key=str(data.get("public_key") or ""),
                private_key=str(data.get("private_key") or ""),
                fingerprint=str(data.get("fingerprint") or ""),
                created_at=float(data.get("created_at") or time.time()),
                device_name=str(data.get("device_name") or "").strip(),
            )
            if identity.local_device_id and identity.public_key and identity.private_key:
                if not identity.fingerprint:
                    identity.fingerprint = self._fingerprint(identity.public_key)
                if not identity.device_name:
                    identity.device_name = self.default_device_name()
                    self._write(identity)
                return identity
        except Exception:
            return None
        return None

    def _create(self) -> DeviceIdentityKey:
        private = x25519.X25519PrivateKey.generate()
        private_bytes = private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        public_key = base64.b64encode(public_bytes).decode("ascii")
        identity = DeviceIdentityKey(
            local_device_id=f"dev_{uuid.uuid4().hex[:16]}",
            public_key=public_key,
            private_key=base64.b64encode(private_bytes).decode("ascii"),
            fingerprint=self._fingerprint(public_key),
            created_at=time.time(),
            device_name=self.default_device_name(),
        )
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._write(identity)
        return identity

    def _write(self, identity: DeviceIdentityKey) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "local_device_id": identity.local_device_id,
                    "public_key": identity.public_key,
                    "private_key": identity.private_key,
                    "fingerprint": identity.fingerprint,
                    "created_at": identity.created_at,
                    "device_name": identity.device_name,
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def update_device_name(self, device_name: str) -> DeviceIdentityKey:
        identity = self.load_or_create()
        normalized = (device_name or "").strip() or self.default_device_name()
        if identity.device_name != normalized:
            identity.device_name = normalized
            self._write(identity)
        return identity

    def default_device_name(self) -> str:
        host = socket.gethostname().split(".")[0] or "Local Device"
        profile = self.config_dir.parent.name if self.config_dir.parent.name else ""
        return f"{host} / {profile}" if profile else host

    def private_key_obj(self) -> x25519.X25519PrivateKey:
        identity = self.load_or_create()
        return x25519.X25519PrivateKey.from_private_bytes(base64.b64decode(identity.private_key))

    @staticmethod
    def public_key_obj(public_key: str) -> x25519.X25519PublicKey:
        return x25519.X25519PublicKey.from_public_bytes(base64.b64decode(public_key))

    @staticmethod
    def _fingerprint(public_key: str) -> str:
        return hashlib.sha256(public_key.encode("utf-8")).hexdigest()[:16]


def profile_from_mapping(data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Extract device profile fields from an online/contact mapping."""
    if not isinstance(data, dict):
        return {}
    return {
        "device_id": str(data.get("device_id") or data.get("deviceId") or "").strip(),
        "device_name": str(data.get("device_name") or data.get("deviceName") or "").strip(),
        "device_public_key": str(data.get("device_public_key") or data.get("devicePublicKey") or "").strip(),
        "device_fingerprint": str(data.get("device_fingerprint") or data.get("deviceFingerprint") or "").strip(),
    }
