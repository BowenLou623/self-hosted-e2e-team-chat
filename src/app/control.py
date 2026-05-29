"""One-shot JSON control CLI for the macOS Launcher.

This module intentionally avoids starting a local HTTP server. The SwiftUI
launcher calls it as `python3 -m src.app.control ...` to inspect and update
profile-local state using the same Python modules as the chat client.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import socket
import urllib.error
import urllib.parse
import urllib.request
import venv
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.app.launch_ticket import LaunchTicketStore
from src.app.runtime_paths import RuntimePaths, normalize_profile_name, profile_paths
from src.identity.local_identity import IdentityManager
from src.storage.sqlite_store import SQLiteStore
from src.sync.settings import SyncSettingsStore
from src.sync.project_index_service import ProjectIndexService
from src.sync.syncthing_client import SyncthingAPIError, SyncthingClient
from src.sync.sync_service import SyncService
from src.ai.settings import AISettingsStore
from src.ai.service import AIService
from src.crypto.device_identity import DeviceIdentityStore
from src.network.discovery import DEFAULT_DISCOVERY_PORT, discover_hubs
from src.network.hub_runtime import ADMIN_USERNAME, require_local_hub_running, runtime_status
from src.network.hub_storage import DESTROY_CONFIRM_PHRASE, HubStorage


DEFAULT_LAUNCHER_SETTINGS = {
    "transport": "network",
    "hub_address": "127.0.0.1:8080",
    "log_level": "INFO",
}

HUB_ADDRESS_RE = re.compile(r"^[^\s:]+:\d{1,5}$")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
        return write_json({"ok": True, **(result or {})})
    except Exception as exc:
        return write_json({"ok": False, "error": str(exc)}, exit_code=1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Instant Messaging Team Launcher control CLI")
    parser.add_argument("--project-root", default=".", help="Python project root")

    subparsers = parser.add_subparsers(dest="command", required=True)

    profile_parser = subparsers.add_parser("profile", help="Profile management")
    profile_sub = profile_parser.add_subparsers(dest="profile_command", required=True)
    profile_list = profile_sub.add_parser("list")
    profile_list.set_defaults(func=cmd_profile_list)
    profile_create = profile_sub.add_parser("create")
    profile_create.add_argument("--profile", required=True)
    profile_create.set_defaults(func=cmd_profile_create)
    profile_inspect = profile_sub.add_parser("inspect")
    profile_inspect.add_argument("--profile", required=True)
    profile_inspect.set_defaults(func=cmd_profile_inspect)

    launcher_parser = subparsers.add_parser("launcher", help="Profile launcher settings")
    launcher_sub = launcher_parser.add_subparsers(dest="launcher_command", required=True)
    launcher_get = launcher_sub.add_parser("get")
    launcher_get.add_argument("--profile", required=True)
    launcher_get.set_defaults(func=cmd_launcher_get)
    launcher_save = launcher_sub.add_parser("save")
    launcher_save.add_argument("--profile", required=True)
    launcher_save.add_argument("--transport", choices=["memory", "network"])
    launcher_save.add_argument("--hub-address")
    launcher_save.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    launcher_save.set_defaults(func=cmd_launcher_save)

    auth_parser = subparsers.add_parser("auth", help="Local profile authentication")
    auth_sub = auth_parser.add_subparsers(dest="auth_command", required=True)
    auth_init = auth_sub.add_parser("init")
    auth_init.add_argument("--profile", required=True)
    auth_init.add_argument("--user-id")
    auth_init.add_argument("--display-name", default="")
    auth_init.set_defaults(func=cmd_auth_init)
    auth_login = auth_sub.add_parser("login")
    auth_login.add_argument("--profile", required=True)
    auth_login.set_defaults(func=cmd_auth_login)

    syncthing_parser = subparsers.add_parser("syncthing", help="Syncthing profile settings")
    syncthing_sub = syncthing_parser.add_subparsers(dest="syncthing_command", required=True)
    syncthing_get = syncthing_sub.add_parser("get")
    syncthing_get.add_argument("--profile", required=True)
    syncthing_get.set_defaults(func=cmd_syncthing_get)
    syncthing_save = syncthing_sub.add_parser("save")
    syncthing_save.add_argument("--profile", required=True)
    syncthing_save.add_argument("--base-url")
    syncthing_save.add_argument("--api-key")
    syncthing_save.add_argument("--timeout-seconds", type=float)
    syncthing_save.set_defaults(func=cmd_syncthing_save)
    syncthing_detect = syncthing_sub.add_parser("detect")
    syncthing_detect.add_argument("--profile", required=True)
    syncthing_detect.set_defaults(func=cmd_syncthing_detect)
    syncthing_test = syncthing_sub.add_parser("test")
    syncthing_test.add_argument("--profile", required=True)
    syncthing_test.add_argument("--base-url")
    syncthing_test.add_argument("--api-key")
    syncthing_test.add_argument("--timeout-seconds", type=float)
    syncthing_test.set_defaults(func=cmd_syncthing_test)

    sync_parser = subparsers.add_parser("sync", help="Project sync status")
    sync_sub = sync_parser.add_subparsers(dest="sync_command", required=True)
    sync_list = sync_sub.add_parser("list")
    sync_list.add_argument("--profile", required=True)
    sync_list.set_defaults(func=cmd_sync_list)
    sync_refresh = sync_sub.add_parser("refresh")
    sync_refresh.add_argument("--profile", required=True)
    sync_refresh.add_argument("--group-id")
    sync_refresh.set_defaults(func=cmd_sync_refresh)
    sync_unbind = sync_sub.add_parser("unbind")
    sync_unbind.add_argument("--profile", required=True)
    sync_unbind.add_argument("--group-id", required=True)
    sync_unbind.add_argument("--local-only", action="store_true")
    sync_unbind.set_defaults(func=cmd_sync_unbind)

    index_parser = subparsers.add_parser("index", help="Project file index")
    index_sub = index_parser.add_subparsers(dest="index_command", required=True)
    index_scan = index_sub.add_parser("scan")
    index_scan.add_argument("--profile", required=True)
    index_scan.add_argument("--group-id", default="")
    index_scan.set_defaults(func=cmd_index_scan)
    index_status = index_sub.add_parser("status")
    index_status.add_argument("--profile", required=True)
    index_status.add_argument("--group-id", default="")
    index_status.set_defaults(func=cmd_index_status)
    index_search = index_sub.add_parser("search")
    index_search.add_argument("--profile", required=True)
    index_search.add_argument("--query", default="")
    index_search.add_argument("--group-id", default="")
    index_search.add_argument("--extension", default="")
    index_search.add_argument("--limit", type=int, default=50)
    index_search.add_argument("--include-missing", action="store_true")
    index_search.set_defaults(func=cmd_index_search)
    index_locate = index_sub.add_parser("locate")
    index_locate.add_argument("--profile", required=True)
    index_locate.add_argument("--file-id", required=True)
    index_locate.set_defaults(func=cmd_index_locate)

    environment_parser = subparsers.add_parser("environment", help="Runtime environment checks")
    environment_sub = environment_parser.add_subparsers(dest="environment_command", required=True)
    environment_check = environment_sub.add_parser("check")
    environment_check.add_argument("--profile")
    environment_check.set_defaults(func=cmd_environment_check)
    environment_verify = environment_sub.add_parser("verify")
    environment_verify.add_argument("--profile")
    environment_verify.add_argument("--venv-path", default="")
    environment_verify.set_defaults(func=cmd_environment_verify)
    environment_bootstrap = environment_sub.add_parser("bootstrap")
    environment_bootstrap.add_argument("--profile", required=True)
    environment_bootstrap.add_argument("--venv-path", default="")
    environment_bootstrap.add_argument("--install-deps", action="store_true")
    environment_bootstrap.add_argument("--verify-client", action="store_true")
    environment_bootstrap.add_argument("--dry-run", action="store_true")
    environment_bootstrap.set_defaults(func=cmd_environment_bootstrap)
    environment_manual = environment_sub.add_parser("manual-commands")
    environment_manual.add_argument("--profile", default="alice")
    environment_manual.add_argument("--venv-path", default="")
    environment_manual.set_defaults(func=cmd_environment_manual_commands)

    security_parser = subparsers.add_parser("security", help="Security and privacy status")
    security_sub = security_parser.add_subparsers(dest="security_command", required=True)
    security_status = security_sub.add_parser("status")
    security_status.add_argument("--profile", required=True)
    security_status.set_defaults(func=cmd_security_status)

    device_parser = subparsers.add_parser("device", help="Profile device identity")
    device_sub = device_parser.add_subparsers(dest="device_command", required=True)
    device_get = device_sub.add_parser("get")
    device_get.add_argument("--profile", required=True)
    device_get.set_defaults(func=cmd_device_get)
    device_save = device_sub.add_parser("save")
    device_save.add_argument("--profile", required=True)
    device_save.add_argument("--device-name", required=True)
    device_save.set_defaults(func=cmd_device_save)

    hub_parser = subparsers.add_parser("hub", help="Hub discovery and local admin helpers")
    hub_sub = hub_parser.add_subparsers(dest="hub_command", required=True)
    hub_discover = hub_sub.add_parser("discover")
    hub_discover.add_argument("--timeout", type=float, default=2.0)
    hub_discover.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT)
    hub_discover.add_argument("--broadcast-address", action="append", default=[])
    hub_discover.set_defaults(func=cmd_hub_discover)
    hub_admin_init = hub_sub.add_parser("admin-init")
    hub_admin_init.add_argument("--password-stdin", action="store_true")
    hub_admin_init.add_argument("--hub-dir", default="")
    hub_admin_init.set_defaults(func=cmd_hub_admin_init)
    hub_admin_login = hub_sub.add_parser("admin-login")
    hub_admin_login.add_argument("--password-stdin", action="store_true")
    hub_admin_login.add_argument("--hub-dir", default="")
    hub_admin_login.set_defaults(func=cmd_hub_admin_login)
    hub_admin_status = hub_sub.add_parser("admin-status")
    hub_admin_status.add_argument("--token", default="")
    hub_admin_status.add_argument("--hub-dir", default="")
    hub_admin_status.set_defaults(func=cmd_hub_admin_status)
    hub_admin_destroy = hub_sub.add_parser("admin-destroy")
    hub_admin_destroy.add_argument("--token", default="")
    hub_admin_destroy.add_argument("--confirm", default="")
    hub_admin_destroy.add_argument("--execute", action="store_true")
    hub_admin_destroy.add_argument("--include-logs", action="store_true")
    hub_admin_destroy.add_argument("--hub-dir", default="")
    hub_admin_destroy.set_defaults(func=cmd_hub_admin_destroy)

    ai_parser = subparsers.add_parser("ai", help="Local AI project assistant")
    ai_sub = ai_parser.add_subparsers(dest="ai_command", required=True)
    ai_settings = ai_sub.add_parser("settings")
    ai_settings_sub = ai_settings.add_subparsers(dest="ai_settings_command", required=True)
    ai_settings_get = ai_settings_sub.add_parser("get")
    ai_settings_get.add_argument("--profile", required=True)
    ai_settings_get.set_defaults(func=cmd_ai_settings_get)
    ai_settings_save = ai_settings_sub.add_parser("save")
    ai_settings_save.add_argument("--profile", required=True)
    ai_settings_save.add_argument("--provider-type")
    ai_settings_save.add_argument("--base-url")
    ai_settings_save.add_argument("--model")
    ai_settings_save.add_argument("--api-key")
    ai_settings_save.add_argument("--timeout-seconds", type=float)
    ai_settings_save.add_argument("--timeout", type=float)
    ai_settings_save.add_argument("--max-file-bytes", type=int)
    ai_settings_save.add_argument("--max-file-kb", type=int)
    ai_settings_save.add_argument("--max-document-bytes", type=int)
    ai_settings_save.add_argument("--max-document-kb", type=int)
    ai_settings_save.add_argument("--auto-load-local-model", choices=["true", "false"])
    ai_settings_save.add_argument("--lmstudio-model-key")
    ai_settings_save.add_argument("--lms-path")
    ai_settings_save.add_argument("--rag-max-context-chars", type=int)
    ai_settings_save.add_argument("--rag-max-chunks", type=int)
    ai_settings_save.add_argument("--conversation-recent-turns", type=int)
    ai_settings_save.add_argument("--embedding-enabled", choices=["true", "false"])
    ai_settings_save.add_argument("--embedding-model")
    ai_settings_save.set_defaults(func=cmd_ai_settings_save)
    ai_test = ai_sub.add_parser("test")
    ai_test.add_argument("--profile", required=True)
    ai_test.set_defaults(func=cmd_ai_test)
    ai_diagnose = ai_sub.add_parser("diagnose")
    ai_diagnose.add_argument("--profile", required=True)
    ai_diagnose.set_defaults(func=cmd_ai_diagnose)
    ai_lmstudio_models = ai_sub.add_parser("lmstudio-models")
    ai_lmstudio_models.add_argument("--profile", required=True)
    ai_lmstudio_models.set_defaults(func=cmd_ai_lmstudio_models)
    ai_project_summary = ai_sub.add_parser("project-summary")
    ai_project_summary.add_argument("--profile", required=True)
    ai_project_summary.add_argument("--group-id", default="")
    ai_project_summary.add_argument("--project-id", default="")
    ai_project_summary.add_argument("--file-id", default="")
    ai_project_summary.add_argument("--include-file-snippets", action="store_true")
    ai_project_summary.set_defaults(func=cmd_ai_project_summary)
    ai_search = ai_sub.add_parser("search-files")
    ai_search.add_argument("--profile", required=True)
    ai_search.add_argument("--query", default="")
    ai_search.add_argument("--group-id", default="")
    ai_search.add_argument("--extension", default="")
    ai_search.add_argument("--limit", type=int, default=30)
    ai_search.set_defaults(func=cmd_ai_search_files)
    ai_file_summary = ai_sub.add_parser("file-summary")
    ai_file_summary.add_argument("--profile", required=True)
    ai_file_summary.add_argument("--file-id", required=True)
    ai_file_summary.set_defaults(func=cmd_ai_file_summary)
    ai_library = ai_sub.add_parser("library")
    ai_library_sub = ai_library.add_subparsers(dest="ai_library_command", required=True)
    ai_library_status = ai_library_sub.add_parser("status")
    ai_library_status.add_argument("--profile", required=True)
    ai_library_status.add_argument("--group-id", default="")
    ai_library_status.add_argument("--project-id", default="")
    ai_library_status.set_defaults(func=cmd_ai_library_status)
    ai_library_build = ai_library_sub.add_parser("build")
    ai_library_build.add_argument("--profile", required=True)
    ai_library_build.add_argument("--group-id", required=True)
    ai_library_build.add_argument("--project-id", default="")
    ai_library_build.set_defaults(func=cmd_ai_library_build)
    ai_library_search = ai_library_sub.add_parser("search")
    ai_library_search.add_argument("--profile", required=True)
    ai_library_search.add_argument("--group-id", default="")
    ai_library_search.add_argument("--project-id", default="")
    ai_library_search.add_argument("--limit", type=int, default=20)
    ai_library_search.set_defaults(func=cmd_ai_library_search)
    ai_library_diagnose = ai_library_sub.add_parser("diagnose")
    ai_library_diagnose.add_argument("--profile", required=True)
    ai_library_diagnose.add_argument("--group-id", required=True)
    ai_library_diagnose.add_argument("--project-id", default="")
    ai_library_diagnose.add_argument("--query", default="")
    ai_library_diagnose.set_defaults(func=cmd_ai_library_diagnose)
    ai_library_list = ai_library_sub.add_parser("list")
    ai_library_list.add_argument("--profile", required=True)
    ai_library_list.add_argument("--group-id", default="")
    ai_library_list.add_argument("--project-id", default="")
    ai_library_list.add_argument("--status", default="")
    ai_library_list.add_argument("--query", default="")
    ai_library_list.add_argument("--limit", type=int, default=100)
    ai_library_list.set_defaults(func=cmd_ai_library_list)
    ai_library_delete = ai_library_sub.add_parser("delete")
    ai_library_delete.add_argument("--profile", required=True)
    ai_library_delete.add_argument("--group-id", default="")
    ai_library_delete.add_argument("--project-id", default="")
    ai_library_delete.add_argument("--source-id", default="")
    ai_library_delete.add_argument("--file-id", default="")
    ai_library_delete.set_defaults(func=cmd_ai_library_delete)
    ai_library_restore = ai_library_sub.add_parser("restore")
    ai_library_restore.add_argument("--profile", required=True)
    ai_library_restore.add_argument("--group-id", default="")
    ai_library_restore.add_argument("--project-id", default="")
    ai_library_restore.add_argument("--source-id", default="")
    ai_library_restore.add_argument("--file-id", default="")
    ai_library_restore.set_defaults(func=cmd_ai_library_restore)
    ai_ask = ai_sub.add_parser("ask")
    ai_ask.add_argument("--profile", required=True)
    ai_ask.add_argument("--group-id", required=True)
    ai_ask.add_argument("--project-id", default="")
    ai_ask.add_argument("--conversation-id", default="")
    ai_ask.set_defaults(func=cmd_ai_ask)
    ai_conversations = ai_sub.add_parser("conversations")
    ai_conversations_sub = ai_conversations.add_subparsers(dest="ai_conversations_command", required=True)
    ai_conversations_list = ai_conversations_sub.add_parser("list")
    ai_conversations_list.add_argument("--profile", required=True)
    ai_conversations_list.add_argument("--group-id", default="")
    ai_conversations_list.add_argument("--project-id", default="")
    ai_conversations_list.set_defaults(func=cmd_ai_conversations_list)
    ai_conversations_show = ai_conversations_sub.add_parser("show")
    ai_conversations_show.add_argument("--profile", required=True)
    ai_conversations_show.add_argument("--conversation-id", required=True)
    ai_conversations_show.set_defaults(func=cmd_ai_conversations_show)
    ai_conversations_clear = ai_conversations_sub.add_parser("clear")
    ai_conversations_clear.add_argument("--profile", required=True)
    ai_conversations_clear.add_argument("--conversation-id", required=True)
    ai_conversations_clear.set_defaults(func=cmd_ai_conversations_clear)
    ai_conversations_delete = ai_conversations_sub.add_parser("delete")
    ai_conversations_delete.add_argument("--profile", required=True)
    ai_conversations_delete.add_argument("--conversation-id", required=True)
    ai_conversations_delete.set_defaults(func=cmd_ai_conversations_delete)

    return parser


def cmd_profile_list(args) -> Dict[str, Any]:
    root = project_root(args)
    profiles_root = root / "runtime" / "profiles"
    profiles: List[Dict[str, Any]] = []
    if profiles_root.exists():
        for child in sorted(profiles_root.iterdir(), key=lambda path: path.name.lower()):
            if child.is_dir():
                profiles.append(profile_summary(profile_paths(child.name, root)))
    return {"profiles": profiles}


def cmd_profile_create(args) -> Dict[str, Any]:
    paths = paths_for(args)
    Path(paths.config_dir).mkdir(parents=True, exist_ok=True)
    (Path(paths.data_dir) / "logs").mkdir(parents=True, exist_ok=True)
    save_launcher_settings(paths, load_launcher_settings(paths))
    return {"profile": profile_summary(paths)}


def cmd_profile_inspect(args) -> Dict[str, Any]:
    return {"profile": profile_summary(paths_for(args))}


def cmd_launcher_get(args) -> Dict[str, Any]:
    paths = paths_for(args)
    return {"settings": load_launcher_settings(paths)}


def cmd_launcher_save(args) -> Dict[str, Any]:
    paths = paths_for(args)
    settings = load_launcher_settings(paths)
    if args.transport is not None:
        settings["transport"] = args.transport
    if args.hub_address is not None:
        hub_address = args.hub_address.strip()
        if not is_valid_hub_address(hub_address):
            raise ValueError("Hub 地址必须使用 host:port 格式")
        settings["hub_address"] = hub_address
    if args.log_level is not None:
        settings["log_level"] = args.log_level
    save_launcher_settings(paths, settings)
    return {"settings": settings}


def cmd_auth_init(args) -> Dict[str, Any]:
    paths = paths_for(args)
    Path(paths.config_dir).mkdir(parents=True, exist_ok=True)
    password = read_password_from_stdin()
    if not password:
        raise ValueError("密码不能为空")

    store = SQLiteStore(paths.db_path)
    try:
        identity_manager = IdentityManager(paths.config_dir, store=store)
        user_id = (args.user_id or "").strip() or identity_manager.generate_user_id()
        if not identity_manager.initialize_identity(user_id, password, args.display_name):
            raise ValueError("初始化 identity 失败")
        current_user = identity_manager.get_current_user()
        if current_user is None:
            raise ValueError("初始化后无法读取 identity")
        ticket = issue_launch_ticket(paths, current_user.user_id, current_user.display_name)
        return {
            "profile": profile_summary(paths),
            "launch_ticket": ticket["token"],
            "expires_at": ticket["expires_at"],
        }
    finally:
        store.cleanup()


def cmd_auth_login(args) -> Dict[str, Any]:
    paths = paths_for(args)
    password = read_password_from_stdin()
    if not password:
        raise ValueError("密码不能为空")

    store = SQLiteStore(paths.db_path)
    try:
        identity_manager = IdentityManager(paths.config_dir, store=store)
        if not identity_manager.load_existing_identity():
            raise ValueError("当前 profile 尚未初始化 identity")
        if not identity_manager.verify_password(password):
            raise ValueError("密码不正确")
        current_user = identity_manager.get_current_user()
        if current_user is None:
            raise ValueError("无法读取当前用户")
        ticket = issue_launch_ticket(paths, current_user.user_id, current_user.display_name)
        return {
            "profile": profile_summary(paths),
            "launch_ticket": ticket["token"],
            "expires_at": ticket["expires_at"],
        }
    finally:
        store.cleanup()


def cmd_syncthing_get(args) -> Dict[str, Any]:
    paths = paths_for(args)
    settings = SyncSettingsStore(paths.config_dir).load()
    return {"settings": settings.to_dict(mask_key=True)}


def cmd_syncthing_save(args) -> Dict[str, Any]:
    paths = paths_for(args)
    settings = SyncSettingsStore(paths.config_dir).save(
        base_url=args.base_url,
        api_key=args.api_key,
        timeout_seconds=args.timeout_seconds,
    )
    return {"settings": settings.to_dict(mask_key=True)}


def cmd_syncthing_detect(args) -> Dict[str, Any]:
    paths = paths_for(args)
    store = SQLiteStore(paths.db_path)
    try:
        user_id = (identity_summary(paths) or {}).get("user_id", "")
        service = SyncService(storage=store, config_dir=paths.config_dir, current_user_id=user_id)
        return {"status": service.detect_local_syncthing()}
    finally:
        store.cleanup()


def cmd_syncthing_test(args) -> Dict[str, Any]:
    paths = paths_for(args)
    settings = SyncSettingsStore(paths.config_dir).load()
    if args.base_url is not None:
        settings.base_url = (args.base_url or "http://127.0.0.1:8384").strip().rstrip("/")
    if args.api_key is not None:
        settings.api_key = (args.api_key or "").strip()
    if args.timeout_seconds is not None:
        settings.timeout_seconds = float(args.timeout_seconds)

    store = SQLiteStore(paths.db_path)
    try:
        user_id = (identity_summary(paths) or {}).get("user_id", "")
        service = SyncService(storage=store, config_dir=paths.config_dir, current_user_id=user_id)
        return {"status": service.test_syncthing_settings(settings)}
    finally:
        store.cleanup()


def cmd_sync_list(args) -> Dict[str, Any]:
    paths = paths_for(args)
    store = SQLiteStore(paths.db_path)
    try:
        return {"items": build_sync_items(store, paths)}
    finally:
        store.cleanup()


def cmd_sync_refresh(args) -> Dict[str, Any]:
    paths = paths_for(args)
    store = SQLiteStore(paths.db_path)
    try:
        user_id = (identity_summary(paths) or {}).get("user_id", "")
        service = SyncService(storage=store, config_dir=paths.config_dir, current_user_id=user_id)
        if args.group_id:
            overview = service.poll_sync_status(args.group_id)
            return {"items": [decorate_sync_overview(store, overview)]}

        items: List[Dict[str, Any]] = []
        for group_id in list_project_sync_group_ids(store):
            overview = service.poll_sync_status(group_id)
            items.append(decorate_sync_overview(store, overview))
        return {"items": items}
    finally:
        store.cleanup()


def cmd_sync_unbind(args) -> Dict[str, Any]:
    paths = paths_for(args)
    store = SQLiteStore(paths.db_path)
    try:
        user_id = (identity_summary(paths) or {}).get("user_id", "")
        service = SyncService(storage=store, config_dir=paths.config_dir, current_user_id=user_id)
        result = service.unbind_group_project(args.group_id, local_only=bool(args.local_only))
        return {"result": result, "items": build_sync_items(store, paths)}
    finally:
        store.cleanup()


def cmd_index_scan(args) -> Dict[str, Any]:
    service, store = index_service_for(args)
    try:
        result = service.scan(group_id=args.group_id or "")
        return {"result": result, "status": service.status(group_id=args.group_id or "")}
    finally:
        store.cleanup()


def cmd_index_status(args) -> Dict[str, Any]:
    service, store = index_service_for(args)
    try:
        return {"status": service.status(group_id=args.group_id or "")}
    finally:
        store.cleanup()


def cmd_index_search(args) -> Dict[str, Any]:
    service, store = index_service_for(args)
    try:
        return {
            "results": service.search(
                query=args.query or "",
                group_id=args.group_id or "",
                extension=args.extension or "",
                limit=args.limit,
                include_missing=bool(args.include_missing),
            )
        }
    finally:
        store.cleanup()


def cmd_index_locate(args) -> Dict[str, Any]:
    service, store = index_service_for(args)
    try:
        result = service.locate(args.file_id)
        if not result:
            raise ValueError("indexed file not found")
        return {"file": result}
    finally:
        store.cleanup()


def cmd_environment_check(args) -> Dict[str, Any]:
    root = project_root(args)
    profile = normalize_profile_name(args.profile) if args.profile else ""
    paths = profile_paths(profile, root) if profile else None
    checks: List[Dict[str, Any]] = []

    def add_check(key: str, title: str, status: str, message: str, repair_hint: str = "") -> None:
        checks.append({
            "key": key,
            "title": title,
            "status": status,
            "message": message,
            "repair_hint": repair_hint,
        })

    root_markers = ["src/app/main.py", "src/app/control.py", "requirements.txt"]
    missing_markers = [marker for marker in root_markers if not (root / marker).exists()]
    if root.exists() and root.is_dir() and not missing_markers:
        add_check("project_root", "Project root", "ok", str(root))
    elif root.exists() and root.is_dir():
        add_check(
            "project_root",
            "Project root",
            "warning",
            f"missing markers: {', '.join(missing_markers)}",
            "Choose the repository root that contains src/app/main.py.",
        )
    else:
        add_check(
            "project_root",
            "Project root",
            "error",
            f"not found: {root}",
            "Choose the Python project root in Launcher Profiles.",
        )

    if root.exists() and os_access_writable(root):
        add_check("runtime_root", "Runtime storage", "ok", "project root is writable")
    else:
        add_check("runtime_root", "Runtime storage", "warning", "project root may not be writable")

    add_check("python", "Python", "ok", f"{sys.executable} ({sys.version.split()[0]})")
    control_spec = importlib.util.find_spec("src.app.control")
    add_check(
        "control_cli",
        "Control CLI",
        "ok" if control_spec else "error",
        "src.app.control importable" if control_spec else "src.app.control is not importable",
        "" if control_spec else "Check project root and PYTHONPATH.",
    )

    for module_name in ["PySide6", "cryptography", "argon2", "sqlite3", "mimetypes"]:
        available = importlib.util.find_spec(module_name) is not None
        add_check(
            f"module_{module_name.lower()}",
            f"Python module {module_name}",
            "ok" if available else "error",
            "available" if available else "missing",
            "" if available else f"Install project dependencies and retry: {module_name}",
        )

    if paths is not None:
        identity = identity_summary(paths)
        if identity:
            add_check(
                "profile_identity",
                "Profile identity",
                "ok",
                f"{identity.get('display_name') or identity.get('user_id') or profile}",
            )
        else:
            add_check(
                "profile_identity",
                "Profile identity",
                "warning",
                "profile has no initialized identity",
                "Create or log into a profile before launching the chat client.",
            )

        db_path = Path(paths.db_path)
        if db_path.exists():
            try:
                conn = sqlite3.connect(db_path)
                conn.execute("SELECT 1")
                conn.close()
                add_check("sqlite", "SQLite database", "ok", str(db_path))
            except sqlite3.Error as exc:
                add_check("sqlite", "SQLite database", "error", str(exc), "Check file permissions or recreate the profile.")
        else:
            add_check("sqlite", "SQLite database", "warning", f"database not found: {db_path}")

        launcher_settings = load_launcher_settings(paths)
        hub_address = str(launcher_settings.get("hub_address") or "")
        if is_valid_hub_address(hub_address):
            reachable, message = probe_tcp(hub_address)
            add_check(
                "hub",
                "Hub",
                "ok" if reachable else "warning",
                message,
                "" if reachable else "Start the Hub or update the Hub address.",
            )
            temp_url = temp_file_status_url(hub_address)
            temp_ok, temp_message = probe_temp_file_service(temp_url)
            add_check(
                "temp_file_service",
                "Temporary file service",
                "ok" if temp_ok else "warning",
                temp_message,
                "" if temp_ok else "Start the Hub with Phase 8 temp file service enabled.",
            )
        else:
            add_check("hub", "Hub", "error", "invalid Hub address", "Use host:port format.")

        ai_settings = AISettingsStore(paths.config_dir).load()
        ai_ready = bool(ai_settings.provider_type and ai_settings.base_url and ai_settings.model)
        add_check(
            "ai_provider",
            "AI provider",
            "ok" if ai_ready else "warning",
            f"{ai_settings.provider_type or 'not selected'} {ai_settings.base_url or ''} {ai_settings.model or ''}".strip(),
            "" if ai_ready else "Choose provider, base URL, and model in AI Project Assistant.",
        )

        sync_settings = SyncSettingsStore(paths.config_dir).load()
        if not sync_settings.api_key:
            add_check(
                "syncthing_api_key",
                "Syncthing API key",
                "warning",
                "API key is not configured",
                "Copy the API key from Syncthing Web UI into Launcher.",
            )
        try:
            status = SyncthingClient.from_settings(sync_settings).get_status()
            device_id = str(status.get("myID") or status.get("myId") or "")
            add_check("syncthing", "Syncthing", "ok", f"connected {device_id}".strip())
        except SyncthingAPIError as exc:
            add_check("syncthing", "Syncthing", "warning", str(exc), "Start Syncthing and verify API settings.")

        store = SQLiteStore(paths.db_path)
        try:
            index_status = ProjectIndexService(store).status()
            add_check(
                "project_index",
                "Project index",
                "ok",
                f"{index_status.get('existing_count', 0)} indexed files",
            )
        except Exception as exc:
            add_check("project_index", "Project index", "warning", str(exc), "Run project indexing again.")
        finally:
            store.cleanup()
    else:
        add_check("profile", "Profile", "warning", "no profile selected", "Select a profile for full checks.")

    overall = "ok"
    if any(item["status"] == "error" for item in checks):
        overall = "error"
    elif any(item["status"] == "warning" for item in checks):
        overall = "warning"

    return {
        "report": {
            "status": overall,
            "project_root": str(root),
            "profile": profile,
            "checked_at": time.time(),
            "checks": checks,
        }
    }


def cmd_environment_verify(args) -> Dict[str, Any]:
    root = project_root(args)
    profile = normalize_profile_name(args.profile) if args.profile else ""
    check_payload = cmd_environment_check(args)
    report = check_payload["report"]
    venv_path = resolve_venv_path(root, args.venv_path)
    steps = environment_steps_from_report(report, venv_path)
    return environment_result(
        status=overall_install_status(steps),
        steps=steps,
        logs=[bootstrap_log("info", "环境验证完成", detail=report.get("status", ""))],
        next_actions=next_actions_for_steps(steps),
        copyable_commands=manual_environment_commands(root, profile or "alice", venv_path),
        extra={
            "report": report,
            "venv_path": str(venv_path),
            "python_executable": venv_python_path(venv_path) if venv_path.exists() else sys.executable,
        },
    )


def cmd_environment_bootstrap(args) -> Dict[str, Any]:
    root = project_root(args)
    profile = normalize_profile_name(args.profile)
    venv_path = resolve_venv_path(root, args.venv_path)
    logs: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []

    def add_step(key: str, title: str, status: str, message: str, repair_hint: str = "") -> None:
        steps.append({
            "key": key,
            "title": title,
            "status": status,
            "message": message,
            "repair_hint": repair_hint,
        })

    if not root.exists():
        add_step("project_root", "Project root", "failed", f"not found: {root}", "Choose the repository root first.")
        return environment_result(
            status="failed",
            steps=steps,
            logs=logs,
            next_actions=next_actions_for_steps(steps),
            copyable_commands=manual_environment_commands(root, profile, venv_path),
        )

    missing_markers = [marker for marker in ["src/app/main.py", "src/app/control.py", "requirements.txt"] if not (root / marker).exists()]
    if missing_markers:
        add_step(
            "project_root",
            "Project root",
            "failed",
            f"missing markers: {', '.join(missing_markers)}",
            "Choose the repository root that contains src/app/main.py.",
        )
        return environment_result(
            status="failed",
            steps=steps,
            logs=logs,
            next_actions=next_actions_for_steps(steps),
            copyable_commands=manual_environment_commands(root, profile, venv_path),
        )
    add_step("project_root", "Project root", "done", str(root))

    profile_paths_value = profile_paths(profile, root)
    if not args.dry_run:
        Path(profile_paths_value.config_dir).mkdir(parents=True, exist_ok=True)
        (Path(profile_paths_value.data_dir) / "logs").mkdir(parents=True, exist_ok=True)
        save_launcher_settings(profile_paths_value, load_launcher_settings(profile_paths_value))
    add_step("profile", "Launcher profile", "done", profile)

    python_version = sys.version.split()[0]
    if sys.version_info < (3, 11):
        add_step(
            "python",
            "Python",
            "needs_action",
            f"{sys.executable} ({python_version})",
            "Python 3.11+ is recommended. Choose a newer Python before installing dependencies.",
        )
    else:
        add_step("python", "Python", "done", f"{sys.executable} ({python_version})")

    command_plan = [
        [sys.executable, "-m", "venv", str(venv_path)],
    ]
    if args.install_deps:
        planned_python = str(venv_python_path(venv_path))
        command_plan.extend([
            [planned_python, "-m", "pip", "install", "--upgrade", "pip"],
            [planned_python, "-m", "pip", "install", "-r", str(root / "requirements.txt")],
        ])

    if args.dry_run:
        add_step("venv", "Virtual environment", "needs_action", f"dry-run: {venv_path}")
        logs.append(bootstrap_log("info", "Dry run only; no files were changed."))
        return environment_result(
            status="needs_action",
            steps=steps,
            logs=logs,
            next_actions=["确认后在 Launcher 中运行自动配置，或复制手动命令执行。"],
            copyable_commands=manual_environment_commands(root, profile, venv_path),
            extra={"planned_commands": [" ".join(command) for command in command_plan]},
        )

    try:
        venv_path.parent.mkdir(parents=True, exist_ok=True)
        logs.append(bootstrap_log("info", "Creating virtual environment", command=" ".join(command_plan[0])))
        venv.EnvBuilder(with_pip=True, clear=False).create(str(venv_path))
        add_step("venv", "Virtual environment", "done", str(venv_path))
    except Exception as exc:
        add_step("venv", "Virtual environment", "failed", str(exc), "Check folder permissions and available disk space.")
        logs.append(bootstrap_log("error", "Failed to create virtual environment", detail=str(exc)))
        return environment_result(
            status="failed",
            steps=steps,
            logs=logs,
            next_actions=next_actions_for_steps(steps),
            copyable_commands=manual_environment_commands(root, profile, venv_path),
        )

    python_path = venv_python_path(venv_path)
    if args.install_deps:
        for command in command_plan[1:]:
            result = run_bootstrap_command(command, root, logs)
            if result.returncode != 0:
                add_step(
                    "dependencies",
                    "Python dependencies",
                    "failed",
                    compact_subprocess_output(result),
                    "Copy the error log, check network access, then retry dependency installation.",
                )
                return environment_result(
                    status="failed",
                    steps=steps,
                    logs=logs,
                    next_actions=next_actions_for_steps(steps),
                    copyable_commands=manual_environment_commands(root, profile, venv_path),
                    extra={"python_executable": str(python_path)},
                )
        add_step("dependencies", "Python dependencies", "done", "requirements.txt installed")
    else:
        add_step("dependencies", "Python dependencies", "skippable", "dependency installation was not requested")

    control_result = run_bootstrap_command(
        [str(python_path), "-m", "src.app.control", "--project-root", str(root), "profile", "list"],
        root,
        logs,
        extra_env={"PYTHONPATH": str(root), "PYTHONUNBUFFERED": "1"},
    )
    add_step(
        "control_cli",
        "Control CLI",
        "done" if control_result.returncode == 0 else "failed",
        "src.app.control verified" if control_result.returncode == 0 else compact_subprocess_output(control_result),
        "" if control_result.returncode == 0 else "Check project root, PYTHONPATH, and installed dependencies.",
    )

    pyside_result = run_bootstrap_command([str(python_path), "-c", "import PySide6; print('PySide6 OK')"], root, logs)
    add_step(
        "pyside6",
        "PySide6",
        "done" if pyside_result.returncode == 0 else "failed",
        "PySide6 import verified" if pyside_result.returncode == 0 else compact_subprocess_output(pyside_result),
        "" if pyside_result.returncode == 0 else "Install requirements.txt or inspect the pip error above.",
    )

    if args.verify_client:
        client_result = run_bootstrap_command(
            [str(python_path), "-c", "import src.app.main; print('client import OK')"],
            root,
            logs,
            extra_env={"PYTHONPATH": str(root), "PYTHONUNBUFFERED": "1"},
        )
        add_step(
            "chat_client",
            "Chat client",
            "done" if client_result.returncode == 0 else "failed",
            "src.app.main import verified" if client_result.returncode == 0 else compact_subprocess_output(client_result),
            "" if client_result.returncode == 0 else "Check Python dependencies before launching the GUI client.",
        )
    else:
        add_step("chat_client", "Chat client", "skippable", "client verification was skipped")

    status = overall_install_status(steps)
    verify_payload: Dict[str, Any] = {}
    if status != "failed":
        verify_args = argparse.Namespace(project_root=str(root), profile=profile, venv_path=str(venv_path))
        verify_payload = cmd_environment_verify(verify_args)
        logs.extend(verify_payload.get("logs", []))

    return environment_result(
        status=status,
        steps=steps,
        logs=logs,
        next_actions=next_actions_for_steps(steps),
        copyable_commands=manual_environment_commands(root, profile, venv_path),
        extra={
            "venv_path": str(venv_path),
            "python_executable": str(python_path),
            "verify": verify_payload.get("report") or verify_payload.get("verify") or {},
        },
    )


def cmd_environment_manual_commands(args) -> Dict[str, Any]:
    root = project_root(args)
    profile = normalize_profile_name(args.profile or "alice")
    venv_path = resolve_venv_path(root, args.venv_path)
    commands = manual_environment_commands(root, profile, venv_path)
    steps = [
        {"key": "manual", "title": "Manual mode", "status": "done", "message": "copy commands into Terminal", "repair_hint": ""},
        {"key": "automatic", "title": "Automatic mode", "status": "skippable", "message": "macOS Launcher can run these checks later", "repair_hint": ""},
    ]
    return environment_result(
        status="done",
        steps=steps,
        logs=[bootstrap_log("info", "Manual commands generated")],
        next_actions=["按顺序执行命令，或回到 Launcher 使用自动配置。"],
        copyable_commands=commands,
        extra={"venv_path": str(venv_path)},
    )


def cmd_security_status(args) -> Dict[str, Any]:
    paths = paths_for(args)
    launcher_settings = load_launcher_settings(paths)
    hub_address = str(launcher_settings.get("hub_address") or DEFAULT_LAUNCHER_SETTINGS["hub_address"])
    temp_url = temp_file_status_url(hub_address) if is_valid_hub_address(hub_address) else ""

    store = SQLiteStore(paths.db_path)
    try:
        user_id = (identity_summary(paths) or {}).get("user_id", "")
        sync_service = SyncService(storage=store, config_dir=paths.config_dir, current_user_id=user_id)
        syncthing_status = sync_service.detect_local_syncthing()
    finally:
        store.cleanup()

    temp_status = fetch_temp_file_service_status(temp_url) if temp_url else {
        "status": "warning",
        "label": "未配置",
        "url": "",
        "message": "Hub 地址无效",
        "ttl_seconds": 0,
        "max_bytes": 0,
        "file_count": 0,
    }

    ai_settings = AISettingsStore(paths.config_dir).load()
    ai_configured = bool(ai_settings.provider_type and ai_settings.base_url and ai_settings.model)
    ai_library_status: Dict[str, Any] = {}
    ai_library_store = SQLiteStore(paths.db_path)
    try:
        ai_library_status = AIService(ai_library_store, ai_settings, profile=paths.profile).document_library_status()
    except Exception as exc:
        ai_library_status = {"status": "warning", "error": str(exc)}
    finally:
        ai_library_store.cleanup()
    report = {
        "checked_at": time.time(),
        "profile": paths.profile,
        "encryption": {
            "current_mode": "direct_encrypted_v2 / group_encrypted_v1",
            "direct_encrypted_v2": importlib.util.find_spec("src.crypto.direct_v2_service") is not None,
            "group_encrypted_v1": importlib.util.find_spec("src.crypto.group_crypto_service") is not None,
            "replay_protection": True,
        },
        "temp_files": temp_status,
        "syncthing": syncthing_status,
        "ai": {
            "status": "configured" if ai_configured else "unconfigured",
            "provider_type": ai_settings.provider_type,
            "provider_label": ai_provider_label(ai_settings.provider_type),
            "provider_location": ai_provider_location(ai_settings.provider_type, ai_settings.base_url),
            "base_url": ai_settings.base_url,
            "model": ai_settings.model,
            "configured": ai_configured,
            "has_api_key": bool(ai_settings.api_key),
            "auto_load_local_model": ai_settings.auto_load_local_model,
            "lmstudio_model_key": ai_settings.lmstudio_model_key,
            "lms_path": ai_settings.lms_path,
            "document_library": ai_library_status,
            "rag_max_context_chars": ai_settings.rag_max_context_chars,
            "rag_max_chunks": ai_settings.rag_max_chunks,
            "embedding_enabled": ai_settings.embedding_enabled,
            "embedding_model": ai_settings.embedding_model,
        },
    }
    return {"report": report}


def cmd_device_get(args) -> Dict[str, Any]:
    paths = paths_for(args)
    return {"device": device_summary(paths, create=True)}


def cmd_device_save(args) -> Dict[str, Any]:
    paths = paths_for(args)
    store = DeviceIdentityStore(paths.config_dir)
    identity = store.update_device_name(args.device_name)
    return {
        "device": {
            "device_id": identity.local_device_id,
            "device_name": identity.device_name,
            "device_fingerprint": identity.fingerprint,
            "device_public_key": identity.public_key,
            "config_path": str(store.path),
        }
    }


def cmd_hub_discover(args) -> Dict[str, Any]:
    hubs = discover_hubs(
        discovery_port=int(args.discovery_port or DEFAULT_DISCOVERY_PORT),
        timeout=float(args.timeout or 2.0),
        broadcast_addresses=args.broadcast_address or None,
    )
    return {"hubs": hubs, "count": len(hubs)}


def cmd_hub_admin_init(args) -> Dict[str, Any]:
    hub_dir = hub_dir_for(args)
    require_local_hub_running(hub_dir)
    password = read_admin_password(args)
    storage = HubStorage(str(hub_dir))
    try:
        result = storage.init_admin(password)
        return {
            "auth": {
                "authenticated": True,
                "initialized": True,
                "admin_username": ADMIN_USERNAME,
                "token": result.get("token", ""),
            }
        }
    finally:
        storage.close()


def cmd_hub_admin_login(args) -> Dict[str, Any]:
    hub_dir = hub_dir_for(args)
    require_local_hub_running(hub_dir)
    password = read_admin_password(args)
    storage = HubStorage(str(hub_dir))
    try:
        result = storage.admin_login(password)
        return {
            "auth": {
                "authenticated": bool(result.get("authenticated")),
                "initialized": True,
                "admin_username": ADMIN_USERNAME,
                "token": result.get("token", ""),
            }
        }
    finally:
        storage.close()


def cmd_hub_admin_status(args) -> Dict[str, Any]:
    hub_dir = hub_dir_for(args)
    storage = HubStorage(str(hub_dir))
    try:
        authenticated = bool(args.token and storage.verify_admin_token(args.token))
        local_status = runtime_status(hub_dir)
        payload = {
            "authenticated": authenticated,
            "destroy_phrase": DESTROY_CONFIRM_PHRASE,
            **local_status,
            "status": storage.status(temp_file_dir=str(hub_dir / "temp_files")),
        }
        if authenticated:
            payload["devices"] = storage.list_devices()
        return {"admin": payload}
    finally:
        storage.close()


def cmd_hub_admin_destroy(args) -> Dict[str, Any]:
    hub_dir = hub_dir_for(args)
    require_local_hub_running(hub_dir)
    storage = HubStorage(str(hub_dir))
    try:
        if not storage.verify_admin_token(args.token):
            storage.record_event("destroy_hub", actor="launcher_control", status="denied")
            raise ValueError("admin token is invalid or missing")
        if args.confirm != DESTROY_CONFIRM_PHRASE:
            storage.record_event("destroy_hub", actor="launcher_control", status="denied", detail={"reason": "bad_confirm"})
            raise ValueError(f'confirmation phrase must be exactly "{DESTROY_CONFIRM_PHRASE}"')
        targets = storage.destroy_plan(include_logs=bool(args.include_logs))
        if not args.execute:
            storage.record_event("destroy_hub", actor="launcher_control", status="dry_run", detail={"target_count": len(targets)})
            return {"result": {"dry_run": True, "deleted": False, "targets": targets}}
        deleted = storage.destroy_hub(include_logs=bool(args.include_logs))
        return {"result": {"dry_run": False, "deleted": True, "targets": deleted}}
    finally:
        try:
            storage.close()
        except Exception:
            pass


def hub_dir_for(args) -> Path:
    root = project_root(args)
    return Path(args.hub_dir).expanduser() if args.hub_dir else root / "runtime" / "hub"


def read_admin_password(args) -> str:
    if not getattr(args, "password_stdin", False):
        raise ValueError("admin password must be provided with --password-stdin")
    password = sys.stdin.read()
    password = password.rstrip("\r\n")
    if not password:
        raise ValueError("admin password is required")
    return password


def cmd_ai_settings_get(args) -> Dict[str, Any]:
    paths = paths_for(args)
    settings = AISettingsStore(paths.config_dir).load()
    return {"settings": settings.to_dict(mask_key=True)}


def cmd_ai_settings_save(args) -> Dict[str, Any]:
    paths = paths_for(args)
    max_file_bytes = args.max_file_bytes
    if max_file_bytes is None and args.max_file_kb is not None:
        max_file_bytes = int(args.max_file_kb) * 1024
    max_document_bytes = args.max_document_bytes
    if max_document_bytes is None and args.max_document_kb is not None:
        max_document_bytes = int(args.max_document_kb) * 1024
    timeout_seconds = args.timeout_seconds
    if timeout_seconds is None and args.timeout is not None:
        timeout_seconds = args.timeout
    settings = AISettingsStore(paths.config_dir).save(
        provider_type=args.provider_type,
        base_url=args.base_url,
        model=args.model,
        api_key=args.api_key,
        timeout_seconds=timeout_seconds,
        max_file_bytes=max_file_bytes,
        max_document_bytes=max_document_bytes,
        auto_load_local_model=(
            None if args.auto_load_local_model is None else args.auto_load_local_model == "true"
        ),
        lmstudio_model_key=args.lmstudio_model_key,
        lms_path=args.lms_path,
        rag_max_context_chars=args.rag_max_context_chars,
        rag_max_chunks=args.rag_max_chunks,
        conversation_recent_turns=args.conversation_recent_turns,
        embedding_enabled=(None if args.embedding_enabled is None else args.embedding_enabled == "true"),
        embedding_model=args.embedding_model,
    )
    return {"settings": settings.to_dict(mask_key=True)}


def cmd_ai_test(args) -> Dict[str, Any]:
    paths = paths_for(args)
    store = SQLiteStore(paths.db_path)
    settings = AISettingsStore(paths.config_dir).load()
    try:
        return {"result": AIService(store, settings).test_connection()}
    finally:
        store.cleanup()


def cmd_ai_diagnose(args) -> Dict[str, Any]:
    paths = paths_for(args)
    store = SQLiteStore(paths.db_path)
    settings = AISettingsStore(paths.config_dir).load()
    try:
        return {"result": AIService(store, settings).diagnose()}
    finally:
        store.cleanup()


def cmd_ai_lmstudio_models(args) -> Dict[str, Any]:
    paths = paths_for(args)
    store = SQLiteStore(paths.db_path)
    settings = AISettingsStore(paths.config_dir).load()
    try:
        return AIService(store, settings).lmstudio_models()
    finally:
        store.cleanup()


def cmd_ai_project_summary(args) -> Dict[str, Any]:
    assistant, store = ai_assistant_for(args)
    try:
        return {
            "result": assistant.generate_project_description(
                group_id=args.group_id or "",
                project_id=args.project_id or "",
                include_file_snippets=bool(args.include_file_snippets),
                file_id=args.file_id or "",
            )
        }
    finally:
        store.cleanup()


def cmd_ai_search_files(args) -> Dict[str, Any]:
    assistant, store = ai_assistant_for(args)
    try:
        return {
            "results": assistant.search_related_files(
                query=args.query or "",
                group_id=args.group_id or "",
                extension=args.extension or "",
                limit=args.limit,
            )
        }
    finally:
        store.cleanup()


def cmd_ai_file_summary(args) -> Dict[str, Any]:
    assistant, store = ai_assistant_for(args)
    try:
        return {"result": assistant.summarize_selected_file(args.file_id)}
    finally:
        store.cleanup()


def cmd_ai_library_status(args) -> Dict[str, Any]:
    assistant, store = ai_assistant_for(args)
    try:
        return {"status": assistant.document_library_status(group_id=args.group_id or "", project_id=args.project_id or "")}
    finally:
        store.cleanup()


def cmd_ai_library_build(args) -> Dict[str, Any]:
    assistant, store = ai_assistant_for(args)
    try:
        return {"result": assistant.build_document_library(group_id=args.group_id or "", project_id=args.project_id or "")}
    finally:
        store.cleanup()


def cmd_ai_library_search(args) -> Dict[str, Any]:
    payload = read_json_from_stdin()
    assistant, store = ai_assistant_for(args)
    try:
        return {
            "result": assistant.search_document_library(
                query=str(payload.get("query") or ""),
                group_id=args.group_id or "",
                project_id=args.project_id or "",
                limit=args.limit,
            )
        }
    finally:
        store.cleanup()


def cmd_ai_library_diagnose(args) -> Dict[str, Any]:
    assistant, store = ai_assistant_for(args)
    try:
        return {
            "result": assistant.diagnose_document_library(
                group_id=args.group_id or "",
                project_id=args.project_id or "",
                query=args.query or "",
            )
        }
    finally:
        store.cleanup()


def cmd_ai_library_list(args) -> Dict[str, Any]:
    assistant, store = ai_assistant_for(args)
    try:
        return {
            "result": assistant.list_document_sources(
                group_id=args.group_id or "",
                project_id=args.project_id or "",
                status=args.status or "",
                query=args.query or "",
                limit=args.limit,
            )
        }
    finally:
        store.cleanup()


def cmd_ai_library_delete(args) -> Dict[str, Any]:
    if not args.source_id and not args.file_id:
        raise ValueError("--source-id 或 --file-id 至少提供一个")
    assistant, store = ai_assistant_for(args)
    try:
        return {
            "result": assistant.delete_document_source(
                source_id=args.source_id or "",
                file_id=args.file_id or "",
                group_id=args.group_id or "",
                project_id=args.project_id or "",
            )
        }
    finally:
        store.cleanup()


def cmd_ai_library_restore(args) -> Dict[str, Any]:
    if not args.source_id and not args.file_id:
        raise ValueError("--source-id 或 --file-id 至少提供一个")
    assistant, store = ai_assistant_for(args)
    try:
        return {
            "result": assistant.restore_document_source(
                source_id=args.source_id or "",
                file_id=args.file_id or "",
                group_id=args.group_id or "",
                project_id=args.project_id or "",
            )
        }
    finally:
        store.cleanup()


def cmd_ai_ask(args) -> Dict[str, Any]:
    payload = read_json_from_stdin()
    assistant, store = ai_assistant_for(args)
    try:
        return {
            "result": assistant.ask_project_question(
                question=str(payload.get("question") or ""),
                group_id=args.group_id or "",
                project_id=args.project_id or "",
                conversation_id=args.conversation_id or "",
            )
        }
    finally:
        store.cleanup()


def cmd_ai_conversations_list(args) -> Dict[str, Any]:
    assistant, store = ai_assistant_for(args)
    try:
        return {"conversations": assistant.list_conversations(group_id=args.group_id or "", project_id=args.project_id or "")}
    finally:
        store.cleanup()


def cmd_ai_conversations_show(args) -> Dict[str, Any]:
    assistant, store = ai_assistant_for(args)
    try:
        return assistant.conversation_detail(args.conversation_id)
    finally:
        store.cleanup()


def cmd_ai_conversations_clear(args) -> Dict[str, Any]:
    assistant, store = ai_assistant_for(args)
    try:
        return {"result": assistant.clear_conversation(args.conversation_id)}
    finally:
        store.cleanup()


def cmd_ai_conversations_delete(args) -> Dict[str, Any]:
    assistant, store = ai_assistant_for(args)
    try:
        return {"result": assistant.delete_conversation(args.conversation_id)}
    finally:
        store.cleanup()


def build_sync_items(store: SQLiteStore, paths: RuntimePaths) -> List[Dict[str, Any]]:
    user_id = (identity_summary(paths) or {}).get("user_id", "")
    service = SyncService(storage=store, config_dir=paths.config_dir, current_user_id=user_id)
    items: List[Dict[str, Any]] = []
    for group_id in list_project_sync_group_ids(store):
        items.append(decorate_sync_overview(store, service.get_group_sync_overview(group_id)))
    return items


def decorate_sync_overview(store: SQLiteStore, overview: Dict[str, Any]) -> Dict[str, Any]:
    group = store.get_group(overview.get("group_id", ""))
    folder = overview.get("shared_folder") or {}
    local_path = folder.get("local_path") or ""
    return {
        **overview,
        "group": group.to_dict() if group else None,
        "local_path_exists": bool(local_path and Path(local_path).is_dir()),
    }


def list_group_ids(store: SQLiteStore) -> List[str]:
    conn = sqlite3.connect(store.db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM groups ORDER BY updated_at DESC")
        return [str(row[0]) for row in cursor.fetchall()]
    finally:
        conn.close()


def list_project_sync_group_ids(store: SQLiteStore) -> List[str]:
    conn = sqlite3.connect(store.db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT group_id
            FROM (
                SELECT group_id, updated_at FROM projects
                WHERE group_id IS NOT NULL AND group_id != ''
                UNION ALL
                SELECT group_id, updated_at FROM shared_folders
                WHERE group_id IS NOT NULL AND group_id != ''
            )
            GROUP BY group_id
            ORDER BY MAX(updated_at) DESC
            """
        )
        return [str(row[0]) for row in cursor.fetchall()]
    finally:
        conn.close()


def profile_summary(paths: RuntimePaths) -> Dict[str, Any]:
    summary = identity_summary(paths)
    device = device_summary(paths, create=False) if summary else {}
    return {
        "profile": paths.profile,
        "exists": Path(paths.data_dir).exists(),
        "data_dir": paths.data_dir,
        "db_path": paths.db_path,
        "config_dir": paths.config_dir,
        "has_identity": bool(summary),
        "user_id": (summary or {}).get("user_id", ""),
        "display_name": (summary or {}).get("display_name", ""),
        "has_password": bool((summary or {}).get("password_hash")),
        "device_id": device.get("device_id", ""),
        "device_name": device.get("device_name", ""),
        "device_fingerprint": device.get("device_fingerprint", ""),
    }


def identity_summary(paths: RuntimePaths) -> Optional[Dict[str, Any]]:
    identity_path = Path(paths.config_dir) / "identity.json"
    if not identity_path.exists():
        return None
    manager = IdentityManager(paths.config_dir)
    return manager.get_stored_identity_summary()


def device_summary(paths: RuntimePaths, create: bool = False) -> Dict[str, Any]:
    store = DeviceIdentityStore(paths.config_dir)
    if not create and not store.path.exists():
        return {
            "device_id": "",
            "device_name": "",
            "device_fingerprint": "",
            "device_public_key": "",
            "config_path": str(store.path),
        }
    identity = store.load_or_create()
    return {
        "device_id": identity.local_device_id,
        "device_name": identity.device_name,
        "device_fingerprint": identity.fingerprint,
        "device_public_key": identity.public_key,
        "config_path": str(store.path),
    }


def load_launcher_settings(paths: RuntimePaths) -> Dict[str, Any]:
    settings_path = launcher_settings_path(paths)
    data: Dict[str, Any] = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            data = {}
    settings = {**DEFAULT_LAUNCHER_SETTINGS, **data}
    if not is_valid_hub_address(str(settings.get("hub_address", ""))):
        settings["hub_address"] = DEFAULT_LAUNCHER_SETTINGS["hub_address"]
    if settings.get("transport") not in {"memory", "network"}:
        settings["transport"] = DEFAULT_LAUNCHER_SETTINGS["transport"]
    return settings


def save_launcher_settings(paths: RuntimePaths, settings: Dict[str, Any]) -> None:
    settings_path = launcher_settings_path(paths)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def launcher_settings_path(paths: RuntimePaths) -> Path:
    return Path(paths.config_dir) / "launcher.json"


def index_service_for(args) -> tuple[ProjectIndexService, SQLiteStore]:
    paths = paths_for(args)
    store = SQLiteStore(paths.db_path)
    return ProjectIndexService(store), store


def ai_assistant_for(args) -> tuple[AIService, SQLiteStore]:
    paths = paths_for(args)
    store = SQLiteStore(paths.db_path)
    settings = AISettingsStore(paths.config_dir).load()
    return AIService(store, settings, profile=paths.profile), store


def issue_launch_ticket(paths: RuntimePaths, user_id: str, display_name: str) -> Dict[str, Any]:
    return LaunchTicketStore(paths.config_dir).issue(paths.profile, user_id, display_name)


def read_password_from_stdin() -> str:
    return sys.stdin.read().rstrip("\n")


def read_json_from_stdin() -> Dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("stdin JSON must be an object")
    return data


def resolve_venv_path(root: Path, raw_path: str = "") -> Path:
    if raw_path:
        path = Path(raw_path).expanduser()
        return path if path.is_absolute() else (root / path).resolve()
    return (root / ".venv").resolve()


def venv_python_path(venv_path: Path) -> Path:
    candidates = [
        venv_path / "bin" / "python",
        venv_path / "bin" / "python3",
        venv_path / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def bootstrap_log(
    level: str,
    message: str,
    command: str = "",
    detail: str = "",
    exit_code: Optional[int] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "level": level,
        "message": message,
        "command": command,
        "detail": detail,
    }
    if exit_code is not None:
        payload["exit_code"] = exit_code
    return payload


def run_bootstrap_command(
    command: List[str],
    root: Path,
    logs: List[Dict[str, Any]],
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    command_text = " ".join(command)
    logs.append(bootstrap_log("info", "Running command", command=command_text))
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        command,
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    detail = compact_subprocess_output(result)
    logs.append(
        bootstrap_log(
            "info" if result.returncode == 0 else "error",
            "Command completed" if result.returncode == 0 else "Command failed",
            command=command_text,
            detail=detail,
            exit_code=int(result.returncode),
        )
    )
    return result


def compact_subprocess_output(result: subprocess.CompletedProcess, max_chars: int = 1800) -> str:
    text = "\n".join(part for part in [result.stdout, result.stderr] if part)
    text = text.strip()
    if not text:
        return f"exit code {result.returncode}"
    return text[-max_chars:]


def environment_result(
    status: str,
    steps: List[Dict[str, Any]],
    logs: List[Dict[str, Any]],
    next_actions: List[str],
    copyable_commands: List[Dict[str, str]],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": status,
        "steps": steps,
        "logs": logs,
        "next_actions": next_actions,
        "copyable_commands": copyable_commands,
    }
    if extra:
        payload.update(extra)
    return payload


def environment_steps_from_report(report: Dict[str, Any], venv_path: Path) -> List[Dict[str, Any]]:
    checks = {str(item.get("key", "")): item for item in report.get("checks", [])}

    def check_status(*keys: str, optional: bool = False) -> tuple[str, str, str]:
        selected = [checks[key] for key in keys if key in checks]
        if not selected:
            return ("skippable" if optional else "needs_action", "未检测", "运行环境检查。")
        if any(item.get("status") == "error" for item in selected):
            first = next(item for item in selected if item.get("status") == "error")
            return "failed", str(first.get("message", "")), str(first.get("repair_hint", ""))
        if any(item.get("status") == "warning" for item in selected):
            first = next(item for item in selected if item.get("status") == "warning")
            return ("skippable" if optional else "needs_action"), str(first.get("message", "")), str(first.get("repair_hint", ""))
        return "done", "已完成", ""

    dependency_keys = [
        "control_cli",
        "module_pyside6",
        "module_cryptography",
        "module_argon2",
        "module_sqlite3",
        "module_mimetypes",
    ]
    profile_status, profile_message, profile_hint = check_status("profile_identity")
    hub_status, hub_message, hub_hint = check_status("hub", optional=True)
    syncthing_status, syncthing_message, syncthing_hint = check_status("syncthing", "syncthing_api_key", optional=True)

    return [
        {
            "key": "welcome",
            "title": "欢迎",
            "status": "done",
            "message": "Team Chat Launcher 安装工作台",
            "repair_hint": "",
        },
        {
            "key": "mode",
            "title": "配置模式",
            "status": "done",
            "message": "自动配置或手动配置均可继续使用",
            "repair_hint": "",
        },
        step_from_check("python", "Python", checks.get("python")),
        step_from_check("project_root", "Project root", checks.get("project_root")),
        {
            "key": "venv",
            "title": "Virtual environment",
            "status": "done" if venv_python_path(venv_path).exists() else "needs_action",
            "message": str(venv_path) if venv_python_path(venv_path).exists() else "尚未创建本地 venv",
            "repair_hint": "" if venv_python_path(venv_path).exists() else "使用自动配置创建 .venv，或复制手动命令。",
        },
        {
            "key": "dependencies",
            "title": "Python dependencies",
            "status": check_status(*dependency_keys)[0],
            "message": check_status(*dependency_keys)[1],
            "repair_hint": check_status(*dependency_keys)[2],
        },
        {
            "key": "profile",
            "title": "Profile identity",
            "status": profile_status,
            "message": profile_message,
            "repair_hint": profile_hint,
        },
        {
            "key": "hub",
            "title": "Hub",
            "status": hub_status,
            "message": hub_message,
            "repair_hint": hub_hint,
        },
        {
            "key": "syncthing",
            "title": "Syncthing",
            "status": syncthing_status,
            "message": syncthing_message,
            "repair_hint": syncthing_hint,
        },
        {
            "key": "complete",
            "title": "完成",
            "status": "done" if report.get("status") == "ok" else "needs_action",
            "message": "可进入 Launcher" if report.get("status") == "ok" else "还有项目需要处理或可稍后配置",
            "repair_hint": "",
        },
    ]


def step_from_check(key: str, title: str, check: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not check:
        return {"key": key, "title": title, "status": "needs_action", "message": "未检测", "repair_hint": "运行环境检查。"}
    status_map = {"ok": "done", "warning": "needs_action", "error": "failed"}
    return {
        "key": key,
        "title": title,
        "status": status_map.get(str(check.get("status", "")), "needs_action"),
        "message": str(check.get("message", "")),
        "repair_hint": str(check.get("repair_hint", "")),
    }


def overall_install_status(steps: List[Dict[str, Any]]) -> str:
    required = [step for step in steps if step.get("status") != "skippable"]
    if any(step.get("status") == "failed" for step in required):
        return "failed"
    if any(step.get("status") == "needs_action" for step in required):
        return "needs_action"
    if any(step.get("status") == "skippable" for step in steps):
        return "skippable"
    return "done"


def next_actions_for_steps(steps: List[Dict[str, Any]]) -> List[str]:
    actions: List[str] = []
    for step in steps:
        if step.get("status") in {"needs_action", "failed"}:
            hint = str(step.get("repair_hint") or "")
            if hint and hint not in actions:
                actions.append(hint)
    return actions or ["所有必需步骤已完成，可以进入 Launcher。"]


def manual_environment_commands(root: Path, profile: str, venv_path: Path) -> List[Dict[str, str]]:
    rel_venv = ".venv" if venv_path == (root / ".venv").resolve() else str(venv_path)
    activate = f". {rel_venv}/bin/activate"
    return [
        {"title": "进入项目目录", "command": f"cd {shell_quote(str(root))}"},
        {"title": "创建 venv", "command": f"python3 -m venv {shell_quote(rel_venv)}"},
        {"title": "启用 venv", "command": activate},
        {"title": "升级 pip", "command": "python -m pip install --upgrade pip"},
        {"title": "安装依赖", "command": "python -m pip install -r requirements.txt"},
        {
            "title": "环境检查",
            "command": f"python -m src.app.control --project-root . environment check --profile {shell_quote(profile)}",
        },
        {"title": "启动 Hub", "command": "./script/phase8.sh hub"},
        {"title": "启动客户端", "command": f"./script/phase8.sh client {shell_quote(profile)}"},
    ]


def shell_quote(value: str) -> str:
    if re.match(r"^[A-Za-z0-9_./:@%+=,-]+$", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def paths_for(args) -> RuntimePaths:
    return profile_paths(normalize_profile_name(args.profile), project_root(args))


def project_root(args) -> Path:
    return Path(args.project_root).expanduser().resolve()


def os_access_writable(path: Path) -> bool:
    try:
        return os.access(path, os.W_OK)
    except Exception:
        return False


def probe_tcp(address: str, timeout_seconds: float = 1.0) -> tuple[bool, str]:
    if not is_valid_hub_address(address):
        return False, "invalid Hub address"
    host, port_text = address.rsplit(":", 1)
    try:
        with socket.create_connection((host, int(port_text)), timeout=timeout_seconds):
            return True, f"reachable: {address}"
    except OSError as exc:
        return False, str(exc)


def temp_file_status_url(hub_address: str) -> str:
    host, port_text = hub_address.rsplit(":", 1)
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{int(port_text) + 1}/status"


def probe_temp_file_service(url: str, timeout_seconds: float = 1.0) -> tuple[bool, str]:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict) and payload.get("status") == "ok":
            return True, f"reachable: {url}, files={payload.get('file_count', 0)}"
        return False, f"unexpected response: {payload}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(exc)
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON: {exc}"


def fetch_temp_file_service_status(url: str, timeout_seconds: float = 1.0) -> Dict[str, Any]:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict) and payload.get("status") == "ok":
            return {
                "status": "ok",
                "label": "可用",
                "url": url,
                "message": f"reachable: {url}",
                "ttl_seconds": int(payload.get("ttl_seconds") or 0),
                "max_bytes": int(payload.get("max_bytes") or 0),
                "file_count": int(payload.get("file_count") or 0),
            }
        return {
            "status": "warning",
            "label": "异常响应",
            "url": url,
            "message": f"unexpected response: {payload}",
            "ttl_seconds": 0,
            "max_bytes": 0,
            "file_count": 0,
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "status": "warning",
            "label": "不可用",
            "url": url,
            "message": str(exc),
            "ttl_seconds": 0,
            "max_bytes": 0,
            "file_count": 0,
        }
    except json.JSONDecodeError as exc:
        return {
            "status": "warning",
            "label": "响应无效",
            "url": url,
            "message": f"invalid JSON: {exc}",
            "ttl_seconds": 0,
            "max_bytes": 0,
            "file_count": 0,
        }


def ai_provider_label(provider_type: str) -> str:
    mapping = {
        "ollama": "Ollama",
        "lm_studio": "LM Studio",
        "openai_compatible": "OpenAI-compatible",
    }
    return mapping.get(provider_type or "", "未选择")


def ai_provider_location(provider_type: str, base_url: str) -> str:
    if provider_type in {"ollama", "lm_studio"}:
        return "local"
    if not base_url:
        return "unknown"
    try:
        parsed = urllib.parse.urlparse(base_url)
        host = (parsed.hostname or "").lower()
    except Exception:
        host = ""
    if host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
        return "local"
    return "remote"


def is_valid_hub_address(value: str) -> bool:
    if not HUB_ADDRESS_RE.match((value or "").strip()):
        return False
    try:
        port = int(value.rsplit(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        return False
    return 1 <= port <= 65535


def write_json(payload: Dict[str, Any], exit_code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
