from __future__ import annotations

from ..models import ConnectionConfig
from .base import RemoteBackend


def make_backend(config: ConnectionConfig) -> RemoteBackend:
    if config.protocol == "ssh":
        from .ssh import SSHBackend

        return SSHBackend(config)
    if config.protocol == "winrm":
        from .winrm import WinRMBackend

        return WinRMBackend(config)
    raise ValueError(f"unsupported protocol: {config.protocol}")
