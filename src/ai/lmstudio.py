"""LM Studio local server/model automation via the bundled lms CLI."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from src.ai.settings import AISettings, resolve_lms_path


class LMStudioError(Exception):
    """LM Studio auto-prepare failed."""

    def __init__(self, message: str, code: str = "lmstudio_error", hint: str = ""):
        super().__init__(message)
        self.code = code
        self.hint = hint


class LMStudioManager:
    """Starts LM Studio local server and loads local models on demand."""

    def __init__(self, settings: AISettings):
        self.settings = settings
        self.lms_path = resolve_lms_path(settings.lms_path)

    def ensure_ready(self) -> Dict[str, Any]:
        if self.settings.provider_type != "lm_studio" or not self.settings.auto_load_local_model:
            return self.diagnose(check_chat=False)

        self._ensure_lms_available()
        server_status = self._server_status()
        if not server_status.get("running"):
            self._run_lms(["server", "start"], timeout=20)

        if self._model_is_loaded():
            report = self.diagnose(check_chat=False)
            report["auto_load_performed"] = False
            return report

        model_key = self.model_key()
        if not self._local_model_exists(model_key):
            raise LMStudioError(
                f"LM Studio 本机模型不存在: {model_key}",
                "model_not_found",
                "请确认该模型已在 LM Studio 下载，或填写正确的本机模型 Key。",
            )

        self._run_lms(
            ["load", model_key, "--identifier", self.settings.model, "--yes"],
            timeout=max(60, int(self.settings.timeout_seconds) + 40),
        )
        if not self._wait_until_model_loaded(timeout_seconds=max(20, int(self.settings.timeout_seconds))):
            raise LMStudioError(
                f"LM Studio 模型已请求加载，但 API 尚未显示可用: {self.settings.model}",
                "model_load_timeout",
                "请稍等模型完成加载，或检查 LM Studio 的运行日志。",
            )
        report = self.diagnose(check_chat=False)
        report["auto_load_performed"] = True
        return report

    def diagnose(self, check_chat: bool = False) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "status": "unknown",
            "provider_type": self.settings.provider_type,
            "base_url": self.settings.base_url,
            "model": self.settings.model,
            "model_key": self.model_key(),
            "lms_path": self.lms_path,
            "server_running": False,
            "model_available": False,
            "loaded_models": [],
            "local_models": [],
            "chat_ok": False,
            "error_code": "",
            "hint": "",
        }
        try:
            self._ensure_lms_available()
            report["server_running"] = bool(self._server_status().get("running"))
            report["local_models"] = self.local_models()
            report["loaded_models"] = self.loaded_model_ids()
            report["model_available"] = self.settings.model in report["loaded_models"]
            report["status"] = "ok" if report["server_running"] and report["model_available"] else "warning"
            if check_chat:
                report["chat_ok"] = self._chat_probe()
        except LMStudioError as exc:
            report["status"] = "error"
            report["error_code"] = exc.code
            report["hint"] = exc.hint or str(exc)
        except Exception as exc:
            report["status"] = "error"
            report["error_code"] = "lmstudio_unknown"
            report["hint"] = str(exc)
        return report

    def local_models(self) -> List[Dict[str, str]]:
        result = self._run_lms(["ls", "--json"], timeout=20)
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise LMStudioError("无法解析 lms ls --json 输出", "invalid_lms_json") from exc
        return self._extract_model_entries(payload)

    def loaded_model_ids(self) -> List[str]:
        try:
            models = self._api_models()
        except LMStudioError:
            return []
        ids: List[str] = []
        for item in models:
            model_id = str(item.get("id") or item.get("model") or "").strip()
            if model_id:
                ids.append(model_id)
        return ids

    def model_key(self) -> str:
        return (self.settings.lmstudio_model_key or self.settings.model or "").strip()

    def _ensure_lms_available(self) -> None:
        result = subprocess.run(
            [self.lms_path, "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise LMStudioError(
                f"无法执行 LM Studio CLI: {self.lms_path}",
                "lms_unavailable",
                "请先安装并打开过 LM Studio，或运行 ~/.lmstudio/bin/lms bootstrap。",
            )

    def _server_status(self) -> Dict[str, Any]:
        result = self._run_lms(["server", "status"], timeout=10, allow_failure=True)
        text = f"{result.stdout}\n{result.stderr}".lower()
        running = result.returncode == 0 and any(marker in text for marker in ["running", "started", "listening", "online"])
        if "not running" in text or "stopped" in text:
            running = False
        if not running:
            try:
                self._api_models()
                running = True
            except LMStudioError:
                pass
        return {"running": running, "raw": (result.stdout or result.stderr or "").strip()}

    def _model_is_loaded(self) -> bool:
        return self.settings.model in self.loaded_model_ids()

    def _wait_until_model_loaded(self, timeout_seconds: int) -> bool:
        deadline = time.time() + max(1, timeout_seconds)
        while time.time() < deadline:
            if self._model_is_loaded():
                return True
            time.sleep(1)
        return self._model_is_loaded()

    def _local_model_exists(self, model_key: str) -> bool:
        if not model_key:
            return False
        normalized_key = model_key.lower()
        for entry in self.local_models():
            candidates = {
                entry.get("key", ""),
                entry.get("id", ""),
                entry.get("path", ""),
                entry.get("name", ""),
                entry.get("display_name", ""),
            }
            if any(value and (value.lower() == normalized_key or normalized_key in value.lower()) for value in candidates):
                return True
        return False

    def _api_models(self) -> List[Dict[str, Any]]:
        base_url = self.settings.base_url.rstrip("/")
        url = f"{base_url}/models" if base_url.endswith("/v1") else f"{base_url}/v1/models"
        request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=max(2, self.settings.timeout_seconds)) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.URLError as exc:
            raise LMStudioError(
                f"无法连接 LM Studio server: {exc.reason}",
                "server_unreachable",
                "请确认 LM Studio Local Server 已启动，或启用自动启动。",
            ) from exc
        except json.JSONDecodeError as exc:
            raise LMStudioError("LM Studio models 响应不是有效 JSON", "invalid_models_json") from exc
        data = payload.get("data") if isinstance(payload, dict) else payload
        return data if isinstance(data, list) else []

    def _chat_probe(self) -> bool:
        return self.settings.model in self.loaded_model_ids()

    def _run_lms(
        self,
        args: List[str],
        timeout: int,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [self.lms_path] + args,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise LMStudioError(
                f"未找到 LM Studio CLI: {self.lms_path}",
                "lms_unavailable",
                "请先安装并打开过 LM Studio，或运行 ~/.lmstudio/bin/lms bootstrap。",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LMStudioError(
                f"LM Studio CLI 超时: lms {' '.join(args)}",
                "lms_timeout",
                "请检查 LM Studio 是否正在响应，或稍后重试。",
            ) from exc
        if result.returncode != 0 and not allow_failure:
            output = (result.stderr or result.stdout or "").strip()
            raise LMStudioError(
                f"LM Studio CLI 执行失败: lms {' '.join(args)}: {output}",
                "lms_command_failed",
                output,
            )
        return result

    def _extract_model_entries(self, payload: Any) -> List[Dict[str, str]]:
        entries: List[Dict[str, str]] = []

        def add_entry(item: Any) -> None:
            if not isinstance(item, dict):
                return
            key = str(item.get("key") or item.get("modelKey") or item.get("path") or item.get("id") or "").strip()
            name = str(item.get("name") or item.get("displayName") or item.get("id") or key).strip()
            path = str(item.get("path") or item.get("modelPath") or key).strip()
            if key or name or path:
                entries.append({
                    "key": key or path or name,
                    "id": str(item.get("id") or key or path or name),
                    "name": name or key or path,
                    "path": path or key or name,
                    "display_name": str(item.get("displayName") or name or key or path),
                })
            for value in item.values():
                if isinstance(value, list):
                    for child in value:
                        add_entry(child)

        if isinstance(payload, list):
            for item in payload:
                add_entry(item)
        elif isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, list):
                    for item in value:
                        add_entry(item)
                elif isinstance(value, dict):
                    add_entry(value)
        return entries
