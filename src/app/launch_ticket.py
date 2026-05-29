"""Short-lived launch tickets for the macOS Launcher."""

import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional


class LaunchTicketError(ValueError):
    """Raised when a launch ticket cannot be consumed."""


class LaunchTicketStore:
    """Issues and consumes one-time local launch tickets.

    Tickets are convenience credentials scoped to this local user account and
    profile config directory. They are not a network session or a replacement
    for the profile password system.
    """

    DEFAULT_TTL_SECONDS = 300

    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir)
        self.ticket_dir = self.config_dir / "launch_tickets"

    def issue(
        self,
        profile: str,
        user_id: str,
        display_name: str = "",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not profile:
            raise ValueError("profile is required")
        if not user_id:
            raise ValueError("user_id is required")

        token = secrets.token_urlsafe(32)
        now = time.time()
        record = {
            "token_hash": self._hash_token(token),
            "profile": profile,
            "user_id": user_id,
            "display_name": display_name or "",
            "created_at": now,
            "expires_at": now + int(ttl_seconds),
            "consumed_at": None,
            "metadata": metadata or {},
        }
        self.ticket_dir.mkdir(parents=True, exist_ok=True)
        self._ticket_path(token).write_text(
            json.dumps(record, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "token": token,
            "expires_at": record["expires_at"],
            "profile": profile,
            "user_id": user_id,
            "display_name": display_name or "",
        }

    def consume(self, token: str, expected_profile: str) -> Dict[str, Any]:
        token = (token or "").strip()
        if not token:
            raise LaunchTicketError("launch ticket is required")

        path = self._ticket_path(token)
        if not path.exists():
            raise LaunchTicketError("launch ticket not found")

        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LaunchTicketError(f"launch ticket is invalid: {exc}") from exc

        if record.get("token_hash") != self._hash_token(token):
            raise LaunchTicketError("launch ticket hash mismatch")
        if record.get("profile") != expected_profile:
            raise LaunchTicketError("launch ticket profile mismatch")
        if record.get("consumed_at"):
            raise LaunchTicketError("launch ticket already consumed")
        if float(record.get("expires_at") or 0) < time.time():
            raise LaunchTicketError("launch ticket expired")

        record["consumed_at"] = time.time()
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        return record

    def _ticket_path(self, token: str) -> Path:
        return self.ticket_dir / f"{self._hash_token(token)}.json"

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
