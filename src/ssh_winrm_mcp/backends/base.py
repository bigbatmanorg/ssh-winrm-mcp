from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..results import RemoteResult


class PersistentRemoteSession(ABC):
    protocol: str
    host: str

    @abstractmethod
    async def execute(self, command: str, *, timeout: float) -> RemoteResult: ...

    @abstractmethod
    async def interrupt(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    def alive(self) -> bool: ...


class RemoteBackend(ABC):
    @abstractmethod
    async def execute(
        self,
        command: str,
        *,
        shell: str = "auto",
        timeout: float = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RemoteResult: ...

    @abstractmethod
    async def upload(
        self, local_path: str, remote_path: str, *, recursive: bool = False
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def download(
        self, remote_path: str, local_path: str, *, recursive: bool = False
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def open_session(self, *, shell: str | None = None) -> PersistentRemoteSession: ...
