"""Hub-local SQLite storage for Phase 11.

The Hub stores routing state, encrypted offline payloads, and admin metadata.
It deliberately does not decrypt or inspect message plaintext.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


DESTROY_CONFIRM_PHRASE = "DESTROY HUB"
PBKDF2_PREFIX = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 260_000


class HubStorage:
    """Small thread-safe SQLite helper for Hub-local state."""

    def __init__(self, hub_dir: str = "runtime/hub", db_path: Optional[str] = None):
        self.hub_dir = Path(hub_dir or "runtime/hub").expanduser()
        self.hub_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path).expanduser() if db_path else self.hub_dir / "hub.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_db(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    device_name TEXT,
                    device_public_key TEXT,
                    device_fingerprint TEXT,
                    first_seen REAL DEFAULT (unixepoch()),
                    last_seen REAL DEFAULT (unixepoch()),
                    trust_status TEXT DEFAULT 'known',
                    metadata TEXT DEFAULT '{}',
                    PRIMARY KEY (user_id, device_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS offline_queue (
                    id TEXT PRIMARY KEY,
                    target_user_id TEXT NOT NULL,
                    target_device_id TEXT DEFAULT '',
                    payload_json TEXT NOT NULL,
                    created_at REAL DEFAULT (unixepoch()),
                    expires_at REAL DEFAULT 0,
                    delivered_at REAL DEFAULT 0
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_offline_queue_target
                ON offline_queue(target_user_id, target_device_id, delivered_at, created_at)
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL DEFAULT (unixepoch())
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS hub_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    actor TEXT DEFAULT '',
                    status TEXT DEFAULT '',
                    detail TEXT DEFAULT '{}',
                    created_at REAL DEFAULT (unixepoch())
                )
                """
            )
            self._conn.commit()

    def upsert_device(self, profile: Dict[str, Any]) -> str:
        """Insert/update a device profile, returning known/new_device/key_changed."""
        user_id = str(profile.get("user_id") or "").strip()
        device_id = str(profile.get("device_id") or "").strip()
        if not user_id or not device_id or device_id.startswith("legacy:"):
            return "legacy"

        now = time.time()
        public_key = str(profile.get("device_public_key") or "").strip()
        fingerprint = str(profile.get("device_fingerprint") or "").strip()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT device_public_key, device_fingerprint FROM devices WHERE user_id = ? AND device_id = ?",
                (user_id, device_id),
            )
            existing = cur.fetchone()
            status = "known"
            trust_status = "known"
            if existing is None:
                status = "new_device"
            elif (
                public_key
                and str(existing["device_public_key"] or "")
                and public_key != str(existing["device_public_key"] or "")
            ):
                status = "key_changed"
                trust_status = "key_changed"

            cur.execute(
                """
                INSERT INTO devices (
                    user_id, device_id, device_name, device_public_key,
                    device_fingerprint, first_seen, last_seen, trust_status, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, device_id) DO UPDATE SET
                    device_name = excluded.device_name,
                    device_public_key = excluded.device_public_key,
                    device_fingerprint = excluded.device_fingerprint,
                    last_seen = excluded.last_seen,
                    trust_status = CASE
                        WHEN devices.trust_status = 'key_changed' THEN devices.trust_status
                        ELSE excluded.trust_status
                    END,
                    metadata = excluded.metadata
                """,
                (
                    user_id,
                    device_id,
                    str(profile.get("device_name") or "").strip(),
                    public_key,
                    fingerprint,
                    now,
                    now,
                    trust_status,
                    json.dumps(profile.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            self._conn.commit()
            return status

    def enqueue_offline(
        self,
        target_user_id: str,
        target_device_id: str,
        payload_json: str,
        ttl_seconds: int = 7 * 24 * 60 * 60,
    ) -> str:
        queue_id = "off_" + secrets.token_urlsafe(18).replace("-", "_")
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO offline_queue (
                    id, target_user_id, target_device_id, payload_json, created_at, expires_at, delivered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    queue_id,
                    str(target_user_id or "").strip(),
                    str(target_device_id or "").strip(),
                    payload_json,
                    now,
                    now + max(60, int(ttl_seconds or 0)),
                ),
            )
            self._conn.commit()
        return queue_id

    def pending_offline(self, target_user_id: str, target_device_id: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "DELETE FROM offline_queue WHERE delivered_at = 0 AND expires_at > 0 AND expires_at <= ?",
                (now,),
            )
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id, payload_json, target_device_id
                FROM offline_queue
                WHERE target_user_id = ?
                  AND delivered_at = 0
                  AND (target_device_id = '' OR target_device_id = ?)
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (str(target_user_id or "").strip(), str(target_device_id or "").strip(), int(limit)),
            )
            rows = [dict(row) for row in cur.fetchall()]
            self._conn.commit()
            return rows

    def mark_offline_delivered(self, queue_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE offline_queue SET delivered_at = ? WHERE id = ?",
                (time.time(), queue_id),
            )
            self._conn.commit()

    def status(self, temp_file_dir: str = "") -> Dict[str, Any]:
        with self._lock:
            cur = self._conn.cursor()
            device_count = cur.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
            offline_count = cur.execute(
                "SELECT COUNT(*) FROM offline_queue WHERE delivered_at = 0"
            ).fetchone()[0]
            event_count = cur.execute("SELECT COUNT(*) FROM hub_events").fetchone()[0]
            admin_initialized = self._get_state("admin_password_hash") is not None
        return {
            "hub_dir": str(self.hub_dir),
            "db_path": str(self.db_path),
            "device_count": int(device_count),
            "offline_queue_count": int(offline_count),
            "event_count": int(event_count),
            "admin_initialized": bool(admin_initialized),
            "temp_file_dir": temp_file_dir,
        }

    def list_devices(self) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT user_id, device_id, device_name, device_public_key, device_fingerprint,
                       first_seen, last_seen, trust_status
                FROM devices
                ORDER BY user_id, last_seen DESC
                """
            )
            return [dict(row) for row in cur.fetchall()]

    def record_event(self, event_type: str, actor: str = "", status: str = "", detail: Optional[Dict[str, Any]] = None) -> None:
        safe_detail = detail or {}
        with self._lock:
            self._conn.execute(
                "INSERT INTO hub_events(event_type, actor, status, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(event_type or ""),
                    str(actor or ""),
                    str(status or ""),
                    json.dumps(safe_detail, ensure_ascii=False, sort_keys=True),
                    time.time(),
                ),
            )
            self._conn.commit()

    def admin_initialized(self) -> bool:
        return self._get_state("admin_password_hash") is not None

    def init_admin(self, password: str) -> Dict[str, Any]:
        if self.admin_initialized():
            raise ValueError("Hub admin already initialized")
        if not password:
            raise ValueError("admin password is required")
        token = secrets.token_urlsafe(32)
        self._set_state("admin_password_hash", hash_password(password))
        self._set_state("admin_token_hash", hash_token(token))
        self._set_state("admin_created_at", str(time.time()))
        self.record_event("admin_init", actor="local_cli", status="ok")
        return {"token": token, "initialized": True}

    def admin_login(self, password: str) -> Dict[str, Any]:
        stored = self._get_state("admin_password_hash")
        if not stored or not verify_password(password, stored):
            self.record_event("admin_login", actor="local_cli", status="denied")
            raise ValueError("invalid admin password")
        token = secrets.token_urlsafe(32)
        self._set_state("admin_token_hash", hash_token(token))
        self._set_state("admin_last_login_at", str(time.time()))
        self.record_event("admin_login", actor="local_cli", status="ok")
        return {"token": token, "authenticated": True}

    def verify_admin_token(self, token: str) -> bool:
        expected = self._get_state("admin_token_hash")
        if not expected or not token:
            return False
        return secrets.compare_digest(expected, hash_token(token))

    def destroy_plan(self, include_logs: bool = False) -> List[str]:
        targets = [self.db_path, self.hub_dir / "temp_files"]
        if include_logs:
            targets.append(self.hub_dir / "logs")
        return [str(path) for path in targets if path.exists()]

    def destroy_hub(self, include_logs: bool = False) -> List[str]:
        targets = self.destroy_plan(include_logs=include_logs)
        self.close()
        for target in targets:
            path = Path(target)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        return targets

    def _get_state(self, key: str) -> Optional[str]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT value FROM admin_state WHERE key = ?", (key,))
            row = cur.fetchone()
            return str(row["value"]) if row else None

    def _set_state(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO admin_state(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, time.time()),
            )
            self._conn.commit()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return (
        f"{PBKDF2_PREFIX}${PBKDF2_ITERATIONS}$"
        f"{base64.b64encode(salt).decode('ascii')}$"
        f"{base64.b64encode(digest).decode('ascii')}"
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        prefix, iterations, salt_b64, digest_b64 = password_hash.split("$", 3)
        if prefix != PBKDF2_PREFIX:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
