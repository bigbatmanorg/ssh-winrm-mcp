from __future__ import annotations

import os
from pathlib import Path


def default_local_root() -> Path:
    """Default directory for uploads, downloads and local scripts."""
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "ssh-winrm-mcp" / "files"


def local_root() -> Path:
    configured = os.getenv("SSH_WINRM_MCP_LOCAL_ROOT")
    root = Path(configured).expanduser() if configured else default_local_root()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def protected_dirs() -> list[Path]:
    # Never let file tools write the inventory or the command plug-in directory: a downloaded
    # .py file there would run on the next remote_command_groups(reload=true).
    from .commands.registry import default_commands_dir
    from .profiles import default_inventory_path

    return [default_inventory_path().parent.resolve(), default_commands_dir().expanduser().resolve()]


def checked_local_path(path: str, *, must_exist: bool = False) -> Path:
    """Resolve a local path for file tools, confined to SSH_WINRM_MCP_LOCAL_ROOT.

    Relative paths are relative to the root. The root defaults to a private data directory;
    an operator can widen it (for example SSH_WINRM_MCP_LOCAL_ROOT=/), but the server's own
    configuration directories always stay off limits.
    """
    root = local_root()
    candidate = (root / Path(path).expanduser()).resolve()
    if not candidate.is_relative_to(root):
        raise PermissionError(f"local path {candidate} is outside SSH_WINRM_MCP_LOCAL_ROOT={root}")
    for protected in protected_dirs():
        if candidate.is_relative_to(protected):
            raise PermissionError(f"local path {candidate} is inside the server's configuration directory")
    if must_exist and not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate
