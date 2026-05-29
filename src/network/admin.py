"""Local-only Hub admin CLI for Phase 11."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from typing import Any, Dict, Optional

from .hub_runtime import ADMIN_USERNAME, require_local_hub_running
from .hub_storage import DESTROY_CONFIRM_PHRASE, HubStorage


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
        print(json.dumps({"ok": True, **(result or {})}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 11 Hub admin CLI")
    parser.add_argument("--hub-dir", default="runtime/hub", help="Hub local directory")
    parser.add_argument("--db-path", default="", help="Optional Hub SQLite path")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize Hub admin password and token")
    init.add_argument("--password-stdin", action="store_true")
    init.set_defaults(func=cmd_init)

    login = sub.add_parser("login", help="Verify admin password and issue a new token")
    login.add_argument("--password-stdin", action="store_true")
    login.set_defaults(func=cmd_login)

    status = sub.add_parser("status", help="Show Hub local status")
    status.add_argument("--token", default="")
    status.set_defaults(func=cmd_status)

    destroy = sub.add_parser("destroy-hub", help="Destroy Hub-local content")
    destroy.add_argument("--token", default="")
    destroy.add_argument("--confirm", default="")
    destroy.add_argument("--execute", action="store_true")
    destroy.add_argument("--include-logs", action="store_true")
    destroy.set_defaults(func=cmd_destroy)
    return parser


def cmd_init(args) -> Dict[str, Any]:
    require_local_hub_running(args.hub_dir)
    password = read_password(args, "Admin password: ")
    storage = storage_for(args)
    try:
        result = storage.init_admin(password)
        return {"admin_username": ADMIN_USERNAME, **result}
    finally:
        storage.close()


def cmd_login(args) -> Dict[str, Any]:
    require_local_hub_running(args.hub_dir)
    password = read_password(args, "Admin password: ")
    storage = storage_for(args)
    try:
        result = storage.admin_login(password)
        return {"admin_username": ADMIN_USERNAME, **result}
    finally:
        storage.close()


def cmd_status(args) -> Dict[str, Any]:
    require_local_hub_running(args.hub_dir)
    storage = storage_for(args)
    try:
        require_admin(storage, args.token)
        return {
            "status": storage.status(temp_file_dir=str(storage.hub_dir / "temp_files")),
            "devices": storage.list_devices(),
        }
    finally:
        storage.close()


def cmd_destroy(args) -> Dict[str, Any]:
    require_local_hub_running(args.hub_dir)
    storage = storage_for(args)
    try:
        require_admin(storage, args.token)
        if args.confirm != DESTROY_CONFIRM_PHRASE:
            storage.record_event("destroy_hub", actor="local_cli", status="denied", detail={"reason": "bad_confirm"})
            raise ValueError(f'confirmation phrase must be exactly "{DESTROY_CONFIRM_PHRASE}"')
        targets = storage.destroy_plan(include_logs=bool(args.include_logs))
        if not args.execute:
            storage.record_event("destroy_hub", actor="local_cli", status="dry_run", detail={"target_count": len(targets)})
            return {"dry_run": True, "deleted": False, "targets": targets}
        deleted = storage.destroy_hub(include_logs=bool(args.include_logs))
        return {"dry_run": False, "deleted": True, "targets": deleted}
    finally:
        try:
            storage.close()
        except Exception:
            pass


def require_admin(storage: HubStorage, token: str) -> None:
    if not storage.verify_admin_token(token):
        storage.record_event("admin_verify", actor="local_cli", status="denied")
        raise ValueError("admin token is invalid or missing")


def storage_for(args) -> HubStorage:
    return HubStorage(args.hub_dir, db_path=args.db_path or None)


def read_password(args, prompt: str) -> str:
    if getattr(args, "password_stdin", False):
        password = sys.stdin.read().rstrip("\r\n")
    else:
        password = getpass.getpass(prompt)
    if not password:
        raise ValueError("admin password is required")
    return password


if __name__ == "__main__":
    sys.exit(main())
