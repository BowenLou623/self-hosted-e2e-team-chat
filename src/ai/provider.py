"""Small adapters for local/OpenAI-compatible chat completion APIs."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from src.ai.lmstudio import LMStudioError, LMStudioManager
from src.ai.settings import AISettings


class AIProviderError(Exception):
    """AI provider request failed."""

    def __init__(self, message: str, code: str = "ai_provider_error", hint: str = ""):
        super().__init__(message)
        self.code = code
        self.hint = hint


class AIProviderClient:
    """Synchronous minimal client used by one-shot control CLI commands."""

    def __init__(self, settings: AISettings):
        self.settings = settings

    def validate(self) -> None:
        if not self.settings.provider_type:
            raise AIProviderError("请先选择 AI provider")
        if self.settings.provider_type not in {"ollama", "lm_studio", "openai_compatible"}:
            raise AIProviderError(f"不支持的 provider_type: {self.settings.provider_type}")
        if not self.settings.base_url:
            raise AIProviderError("请配置 API Base URL")
        if not self.settings.model:
            raise AIProviderError("请配置 model name")

    def test_connection(self) -> Dict[str, Any]:
        content = self.chat([
            {"role": "system", "content": "Reply with a short connection status."},
            {"role": "user", "content": "Say OK."},
        ])
        return {"status": "ok", "reply": content[:500]}

    def chat(self, messages: List[Dict[str, str]], options: Optional[Dict[str, Any]] = None) -> str:
        self.validate()
        if self.settings.provider_type == "ollama":
            return self._chat_ollama(messages, options=options)
        return self._chat_openai_compatible(messages, options=options)

    def embed(self, texts: List[str], options: Optional[Dict[str, Any]] = None) -> List[List[float]]:
        raise AIProviderError("Embedding is reserved for Phase 10 and is disabled in Phase 9.", "embedding_disabled")

    def _chat_openai_compatible(self, messages: List[Dict[str, str]], options: Optional[Dict[str, Any]] = None) -> str:
        if self.settings.provider_type == "lm_studio" and self.settings.auto_load_local_model:
            self._ensure_lmstudio_ready()
        base_url = self.settings.base_url.rstrip("/")
        endpoint = f"{base_url}/chat/completions" if base_url.endswith("/v1") else f"{base_url}/v1/chat/completions"
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": float((options or {}).get("temperature", 0.2)),
        }
        try:
            data = self._post_json(endpoint, payload)
        except AIProviderError as exc:
            if (
                self.settings.provider_type == "lm_studio"
                and self.settings.auto_load_local_model
                and exc.code == "http_503"
            ):
                self._ensure_lmstudio_ready()
                data = self._post_json(endpoint, payload)
            else:
                raise
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception as exc:
            raise AIProviderError(f"无法解析 AI 响应: {data}") from exc

    def _ensure_lmstudio_ready(self) -> None:
        try:
            LMStudioManager(self.settings).ensure_ready()
        except LMStudioError as exc:
            message = str(exc)
            if exc.hint:
                message = f"{message}。{exc.hint}"
            raise AIProviderError(message, exc.code, exc.hint) from exc

    def _chat_ollama(self, messages: List[Dict[str, str]], options: Optional[Dict[str, Any]] = None) -> str:
        endpoint = f"{self.settings.base_url.rstrip('/')}/api/chat"
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": float((options or {}).get("temperature", 0.2))},
        }
        data = self._post_json(endpoint, payload)
        if isinstance(data.get("message"), dict):
            return str(data["message"].get("content") or "").strip()
        if data.get("response"):
            return str(data.get("response")).strip()
        raise AIProviderError(f"无法解析 Ollama 响应: {data}")

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            try:
                details = exc.read().decode("utf-8")
            except Exception:
                details = str(exc)
            if self.settings.provider_type == "lm_studio" and exc.code == 503:
                raise AIProviderError(
                    "LM Studio server 已响应，但模型当前不可用或尚未加载。",
                    "http_503",
                    "请确认自动加载已开启，且本机模型 Key 与 Model id 正确。",
                ) from exc
            raise AIProviderError(f"AI HTTP {exc.code}: {details}", f"http_{exc.code}") from exc
        except urllib.error.URLError as exc:
            if self.settings.provider_type == "lm_studio":
                raise AIProviderError(
                    f"无法连接 LM Studio server: {exc.reason}",
                    "server_unreachable",
                    "请确认 LM Studio Local Server 可用，或开启自动启动并加载本机模型。",
                ) from exc
            raise AIProviderError(f"AI 连接失败: {exc.reason}", "connection_failed") from exc
        except TimeoutError as exc:
            raise AIProviderError("AI 请求超时", "timeout") from exc
        except json.JSONDecodeError as exc:
            raise AIProviderError("AI 响应不是有效 JSON", "invalid_json") from exc
