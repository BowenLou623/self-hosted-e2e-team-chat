"""Runtime path helpers shared by the Python app and macOS launcher control CLI."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


DEFAULT_PROFILE_ROOT = os.path.join("runtime", "profiles")


@dataclass(frozen=True)
class RuntimePaths:
    """Application runtime paths. A profile only selects local storage paths."""

    profile: str
    data_dir: str
    db_path: str
    config_dir: str


def normalize_profile_name(profile: str) -> str:
    """Validate a profile name so it cannot be used as a path."""
    normalized = (profile or "").strip()
    if not normalized:
        raise ValueError("--profile 不能为空")
    if normalized in {".", ".."}:
        raise ValueError("--profile 不能是 . 或 ..")
    if os.path.isabs(normalized) or os.sep in normalized or (os.altsep and os.altsep in normalized):
        raise ValueError("--profile 只能是本地配置档名称，不能包含路径分隔符")
    return normalized


def profile_paths(profile: str, project_root: Optional[Union[str, os.PathLike]] = None) -> RuntimePaths:
    """Return the canonical runtime paths for a profile under a project root."""
    profile_name = normalize_profile_name(profile)
    root = Path(project_root or os.getcwd()).expanduser().resolve()
    data_dir = root / DEFAULT_PROFILE_ROOT / profile_name
    return RuntimePaths(
        profile=profile_name,
        data_dir=str(data_dir),
        db_path=str(data_dir / "chat.db"),
        config_dir=str(data_dir / "config"),
    )


def resolve_runtime_paths(args, config) -> RuntimePaths:
    """Resolve runtime paths from CLI args and config, preserving existing defaults."""
    profile_name = normalize_profile_name(args.profile) if args.profile is not None else ""

    if args.data_dir:
        data_dir = args.data_dir
    elif profile_name:
        data_dir = os.path.join(DEFAULT_PROFILE_ROOT, profile_name)
    else:
        data_dir = config.data_dir

    data_dir = os.path.abspath(data_dir)

    if args.db_path:
        db_path = args.db_path
    elif args.data_dir or profile_name:
        db_path = os.path.join(data_dir, "chat.db")
    else:
        db_path = config.get_db_path()

    config_dir = args.config_dir or os.path.join(data_dir, "config")

    return RuntimePaths(
        profile=profile_name,
        data_dir=data_dir,
        db_path=os.path.abspath(db_path),
        config_dir=os.path.abspath(config_dir),
    )
