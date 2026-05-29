"""Local Syncthing API settings for the current app profile."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_SYNCTHING_API_URL = "http://127.0.0.1:8384"


@dataclass
class SyncthingSettings:
    base_url: str = DEFAULT_SYNCTHING_API_URL
    api_key: str = ""
    timeout_seconds: float = 2.0

    def to_dict(self, mask_key: bool = False) -> Dict[str, Any]:
        api_key = self.api_key
        if mask_key and api_key:
            api_key = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "********"
        return {
            "base_url": self.base_url,
            "api_key": api_key,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncthingSettings":
        return cls(
            base_url=(data.get("base_url") or DEFAULT_SYNCTHING_API_URL).strip().rstrip("/"),
            api_key=(data.get("api_key") or "").strip(),
            timeout_seconds=float(data.get("timeout_seconds") or 2.0),
        )


class SyncSettingsStore:
    """Stores Syncthing settings in config_dir/sync.json."""

    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir or "data/config")
        self.settings_path = self.config_dir / "sync.json"

    def load(self) -> SyncthingSettings:
        data: Dict[str, Any] = {}
        if self.settings_path.exists():
            try:
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                data = {}

        settings = SyncthingSettings.from_dict(data)

        env_url = os.getenv("CHAT_SYNCTHING_API_URL") or os.getenv("SYNCTHING_API_URL")
        env_key = os.getenv("CHAT_SYNCTHING_API_KEY") or os.getenv("SYNCTHING_API_KEY")
        if env_url:
            settings.base_url = env_url.strip().rstrip("/")
        if env_key:
            settings.api_key = env_key.strip()
        return settings

    def save(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> SyncthingSettings:
        current = self.load()
        if base_url is not None:
            current.base_url = (base_url or DEFAULT_SYNCTHING_API_URL).strip().rstrip("/")
        if api_key is not None:
            current.api_key = (api_key or "").strip()
        if timeout_seconds is not None:
            current.timeout_seconds = float(timeout_seconds)

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(current.to_dict(mask_key=False), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return current
