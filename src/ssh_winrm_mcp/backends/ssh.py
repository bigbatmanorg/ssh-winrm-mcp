from __future__ import annotations

import asyncio
import contextlib
import re
import shlex
import time
import uuid
from pathlib import Path
from typing import Any

import asyncssh

from ..models import ConnectionConfig
from ..results import RemoteResult
from .base import PersistentRemoteSession, RemoteBackend


class SSHBackend(RemoteBackend):
    def __init__(self, config: ConnectionConfig) -> None:
        self.config = config

    async def _connect(self) -> asyncssh.SSHClientConnection:
        cfg = self.config
        kwargs: dict[str, Any] = {
            "host": cfg.host,
            "port": cfg.effective_port,
            "username": cfg.username,
            "connect_timeout": cfg.connect_timeout,
        }
        password = cfg.resolved_password()
        if password is not None:
            kwargs["password"] = password
        if cfg.private_key_path:
            kwargs["client_keys"] = [str(Path(cfg.private_key_path).expanduser())]
            passphrase = cfg.resolved_key_passphrase()
            if passphrase is not None:
                kwargs["passphrase"] = passphrase
        elif not cfg.ssh_agent:
            kwargs["client_keys"] = []
            kwargs["agent_path"] = None

        if not cfg.verify_host_key:
            kwargs["known_hosts"] = None
        elif cfg.known_hosts_path:
            kwargs["known_hosts"] = str(Path(cfg.known_hosts_path).expanduser())

        return await asyncssh.connect(**kwargs)

    async def execute(
        self,
        command: str,
        *,
        shell: str = "auto",
        timeout: float = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RemoteResult:
        rendered = _prepare_shell_command(command, cwd=cwd, env=env)
        started = time.monotonic()
        conn = await self._connect()
        try:
            result = await conn.run(rendered, check=False, timeout=timeout)
            code = int(result.exit_status) if result.exit_status is not None else -1
            return RemoteResult(
                protocol="ssh",
                host=self.config.host,
                command=command,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=code,
                duration_seconds=round(time.monotonic() - started, 3),
                ok=code == 0,
            )
        finally:
            conn.close()
            await conn.wait_closed()

    async def upload(self, local_path: str, remote_path: str, *, recursive: bool = False) -> dict[str, Any]:
        conn = await self._connect()
        try:
            async with conn.start_sftp_client() as sftp:
                await sftp.put(local_path, remote_path, preserve=True, recurse=recursive)
            return {
                "ok": True,
                "protocol": "ssh",
                "host": self.config.host,
                "direction": "upload",
                "local_path": local_path,
                "remote_path": remote_path,
                "recursive": recursive,
            }
        finally:
            conn.close()
            await conn.wait_closed()

    async def download(self, remote_path: str, local_path: str, *, recursive: bool = False) -> dict[str, Any]:
        conn = await self._connect()
        try:
            async with conn.start_sftp_client() as sftp:
                await sftp.get(remote_path, local_path, preserve=True, recurse=recursive)
            return {
                "ok": True,
                "protocol": "ssh",
                "host": self.config.host,
                "direction": "download",
                "remote_path": remote_path,
                "local_path": local_path,
                "recursive": recursive,
            }
        finally:
            conn.close()
            await conn.wait_closed()

    async def open_session(self, *, shell: str | None = None) -> PersistentRemoteSession:
        conn = await self._connect()
        try:
            process = await conn.create_process(shell or "sh", encoding="utf-8")
            session = SSHPersistentSession(conn, process, self.config.host)
            # Consume login banners and verify that the shell understands our completion marker.
            await session.initialize()
            return session
        except Exception:
            conn.close()
            await conn.wait_closed()
            raise


class SSHPersistentSession(PersistentRemoteSession):
    protocol = "ssh"

    def __init__(
        self,
        connection: asyncssh.SSHClientConnection,
        process: asyncssh.SSHClientProcess[str],
        host: str,
    ) -> None:
        self.host = host
        self._connection = connection
        self._process = process
        self._lock = asyncio.Lock()
        self._closed = False
        self._stdout_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._stderr_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._stdout_pending = ""
        self._stderr_pending = ""
        self._pumps = [
            asyncio.create_task(self._pump(process.stdout, self._stdout_queue)),
            asyncio.create_task(self._pump(process.stderr, self._stderr_queue)),
        ]

    async def initialize(self) -> None:
        await self.execute(":", timeout=30)

    @staticmethod
    async def _pump(reader: Any, queue: asyncio.Queue[str | None]) -> None:
        try:
            while True:
                chunk = await reader.read(8192)
                if not chunk:
                    break
                await queue.put(chunk)
        finally:
            await queue.put(None)

    async def execute(self, command: str, *, timeout: float) -> RemoteResult:
        async with self._lock:
            if not self.alive():
                raise RuntimeError("SSH session is closed")

            token = uuid.uuid4().hex
            marker = f"__SSH_WINRM_MCP_DONE_{token}__"
            payload = f"{command}\n__ssh_winrm_mcp_rc=$?\nprintf '\\n{marker}:%s\\n' \"$__ssh_winrm_mcp_rc\"\n"
            started = time.monotonic()
            self._process.stdin.write(payload)

            try:
                stdout, code = await asyncio.wait_for(self._read_stdout_until(marker), timeout=timeout)
            except asyncio.TimeoutError as exc:
                await self.interrupt()
                raise TimeoutError(
                    f"SSH session command exceeded {timeout} seconds; the session was closed to stop it"
                ) from exc

            # Stderr is a separate channel without a PTY. Give the final packets a moment to arrive.
            await asyncio.sleep(0.03)
            stderr = self._drain_stderr()
            return RemoteResult(
                protocol="ssh",
                host=self.host,
                command=command,
                stdout=stdout,
                stderr=stderr,
                exit_code=code,
                duration_seconds=round(time.monotonic() - started, 3),
                ok=code == 0,
            )

    async def _read_stdout_until(self, marker: str) -> tuple[str, int]:
        pattern = re.compile(rf"(?:\r?\n)?{re.escape(marker)}:(-?\d+)\r?\n")
        buffer = self._stdout_pending
        self._stdout_pending = ""
        while True:
            match = pattern.search(buffer)
            if match:
                output = buffer[: match.start()]
                self._stdout_pending = buffer[match.end() :]
                return output, int(match.group(1))
            chunk = await self._stdout_queue.get()
            if chunk is None:
                raise RuntimeError("remote SSH shell ended before returning a command result")
            buffer += chunk

    def _drain_stderr(self) -> str:
        chunks = [self._stderr_pending]
        self._stderr_pending = ""
        while True:
            try:
                chunk = self._stderr_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if chunk is None:
                continue
            chunks.append(chunk)
        return "".join(chunks)

    async def interrupt(self) -> None:
        # A shell without a PTY cannot reliably receive Ctrl-C. Closing the channel is deterministic.
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self._process.stdin.write_eof()
        with contextlib.suppress(Exception):
            self._process.close()
        with contextlib.suppress(Exception):
            await self._process.wait_closed()
        self._connection.close()
        with contextlib.suppress(Exception):
            await self._connection.wait_closed()
        for task in self._pumps:
            task.cancel()
        await asyncio.gather(*self._pumps, return_exceptions=True)

    def alive(self) -> bool:
        if self._closed:
            return False
        try:
            return not self._connection.is_closed()
        except AttributeError:
            return True


def _prepare_shell_command(command: str, *, cwd: str | None, env: dict[str, str] | None) -> str:
    parts: list[str] = []
    if cwd:
        parts.append(f"cd -- {shlex.quote(cwd)}")
    if env:
        assignments: list[str] = []
        for key, value in env.items():
            _validate_env_name(key)
            assignments.append(f"{key}={shlex.quote(str(value))}")
        parts.append(f"export {' '.join(assignments)}")
    parts.append(command)
    return " && ".join(parts)


def _validate_env_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"invalid environment variable name: {name!r}")
