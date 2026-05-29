"""Profile-local AI provider settings."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


SUPPORTED_AI_PROVIDERS = {"", "ollama", "lm_studio", "openai_compatible"}
DEFAULT_AI_BASE_URLS = {
    "ollama": "http://127.0.0.1:11434",
    "lm_studio": "http://127.0.0.1:1234/v1",
    "openai_compatible": "",
}


@dataclass
class AISettings:
    provider_type: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_seconds: float = 20.0
    max_file_bytes: int = 200 * 1024
    max_document_bytes: int = 1024 * 1024
    auto_load_local_model: bool = True
    lmstudio_model_key: str = ""
    lms_path: str = ""
    rag_max_context_chars: int = 12000
    rag_max_chunks: int = 8
    conversation_recent_turns: int = 6
    embedding_enabled: bool = False
    embedding_model: str = ""

    def with_provider_defaults(self) -> "AISettings":
        if self.provider_type and not self.base_url:
            self.base_url = DEFAULT_AI_BASE_URLS.get(self.provider_type, "")
        return self

    def to_dict(self, mask_key: bool = False) -> Dict[str, Any]:
        api_key = self.api_key
        if mask_key and api_key:
            api_key = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "********"
        return {
            "provider_type": self.provider_type,
            "base_url": self.base_url,
            "model": self.model,
            "api_key": api_key,
            "timeout_seconds": self.timeout_seconds,
            "max_file_bytes": self.max_file_bytes,
            "max_document_bytes": self.max_document_bytes,
            "auto_load_local_model": self.auto_load_local_model,
            "lmstudio_model_key": self.lmstudio_model_key,
            "lms_path": self.lms_path,
            "rag_max_context_chars": self.rag_max_context_chars,
            "rag_max_chunks": self.rag_max_chunks,
            "conversation_recent_turns": self.conversation_recent_turns,
            "embedding_enabled": self.embedding_enabled,
            "embedding_model": self.embedding_model,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AISettings":
        provider_type = str(data.get("provider_type") or "").strip()
        if provider_type not in SUPPORTED_AI_PROVIDERS:
            provider_type = ""
        return cls(
            provider_type=provider_type,
            base_url=str(data.get("base_url") or "").strip().rstrip("/"),
            model=str(data.get("model") or "").strip(),
            api_key=str(data.get("api_key") or "").strip(),
            timeout_seconds=float(data.get("timeout_seconds") or 20.0),
            max_file_bytes=max(1024, min(int(data.get("max_file_bytes") or 200 * 1024), 1024 * 1024)),
            max_document_bytes=max(1024, min(int(data.get("max_document_bytes") or 1024 * 1024), 2 * 1024 * 1024)),
            auto_load_local_model=bool(data.get("auto_load_local_model", True)),
            lmstudio_model_key=str(data.get("lmstudio_model_key") or "").strip(),
            lms_path=str(data.get("lms_path") or "").strip(),
            rag_max_context_chars=max(2000, min(int(data.get("rag_max_context_chars") or 12000), 40000)),
            rag_max_chunks=max(1, min(int(data.get("rag_max_chunks") or 8), 20)),
            conversation_recent_turns=max(1, min(int(data.get("conversation_recent_turns") or 6), 20)),
            embedding_enabled=bool(data.get("embedding_enabled", False)),
            embedding_model=str(data.get("embedding_model") or "").strip(),
        ).with_provider_defaults()


class AISettingsStore:
    """Stores AI settings in config_dir/ai.json."""

    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir or "data/config")
        self.path = self.config_dir / "ai.json"

    def load(self) -> AISettings:
        data: Dict[str, Any] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                data = {}
        return AISettings.from_dict(data)

    def save(
        self,
        provider_type: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        max_file_bytes: Optional[int] = None,
        max_document_bytes: Optional[int] = None,
        auto_load_local_model: Optional[bool] = None,
        lmstudio_model_key: Optional[str] = None,
        lms_path: Optional[str] = None,
        rag_max_context_chars: Optional[int] = None,
        rag_max_chunks: Optional[int] = None,
        conversation_recent_turns: Optional[int] = None,
        embedding_enabled: Optional[bool] = None,
        embedding_model: Optional[str] = None,
    ) -> AISettings:
        current = self.load()
        if provider_type is not None:
            current.provider_type = provider_type.strip()
            if current.provider_type not in SUPPORTED_AI_PROVIDERS:
                raise ValueError("provider_type must be ollama, lm_studio, or openai_compatible")
            if not current.base_url:
                current.base_url = DEFAULT_AI_BASE_URLS.get(current.provider_type, "")
        if base_url is not None:
            current.base_url = base_url.strip().rstrip("/")
        if model is not None:
            current.model = model.strip()
        if api_key is not None:
            current.api_key = api_key.strip()
        if timeout_seconds is not None:
            current.timeout_seconds = float(timeout_seconds)
        if max_file_bytes is not None:
            current.max_file_bytes = max(1024, min(int(max_file_bytes), 1024 * 1024))
        if max_document_bytes is not None:
            current.max_document_bytes = max(1024, min(int(max_document_bytes), 2 * 1024 * 1024))
        if auto_load_local_model is not None:
            current.auto_load_local_model = bool(auto_load_local_model)
        if lmstudio_model_key is not None:
            current.lmstudio_model_key = lmstudio_model_key.strip()
        if lms_path is not None:
            current.lms_path = lms_path.strip()
        if rag_max_context_chars is not None:
            current.rag_max_context_chars = max(2000, min(int(rag_max_context_chars), 40000))
        if rag_max_chunks is not None:
            current.rag_max_chunks = max(1, min(int(rag_max_chunks), 20))
        if conversation_recent_turns is not None:
            current.conversation_recent_turns = max(1, min(int(conversation_recent_turns), 20))
        if embedding_enabled is not None:
            current.embedding_enabled = bool(embedding_enabled)
        if embedding_model is not None:
            current.embedding_model = embedding_model.strip()

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(current.to_dict(mask_key=False), indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return current


def resolve_lms_path(configured_path: str = "") -> str:
    """Resolve LM Studio's CLI without mutating shell PATH."""
    configured = (configured_path or "").strip()
    if configured and Path(configured).expanduser().exists():
        return str(Path(configured).expanduser())
    discovered = shutil.which("lms")
    if discovered:
        return discovered
    fallback = Path.home() / ".lmstudio" / "bin" / "lms"
    if fallback.exists():
        return str(fallback)
    return configured or "lms"
