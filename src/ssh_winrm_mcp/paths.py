from __future__ import annotations

import os
from pathlib import Path


def checked_local_path(path: str, *, must_exist: bool = False) -> Path:
    candidate = Path(path).expanduser().resolve()
    root_value = os.getenv("SSH_WINRM_MCP_LOCAL_ROOT")
    if root_value:
        root = Path(root_value).expanduser().resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PermissionError(f"local path {candidate} is outside SSH_WINRM_MCP_LOCAL_ROOT={root}") from exc
    if must_exist and not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate
