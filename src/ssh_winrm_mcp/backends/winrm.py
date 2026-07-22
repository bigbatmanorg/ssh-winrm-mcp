from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from pypsrp.client import Client
from pypsrp.powershell import PowerShell, RunspacePool
from pypsrp.wsman import WSMan

from ..models import ConnectionConfig
from ..results import RemoteResult
from .base import PersistentRemoteSession, RemoteBackend


class WinRMBackend(RemoteBackend):
    def __init__(self, config: ConnectionConfig) -> None:
        self.config = config

    def _kwargs(self) -> dict[str, Any]:
        cfg = self.config
        kwargs: dict[str, Any] = {
            "port": cfg.effective_port,
            "username": cfg.username,
            "password": cfg.resolved_password(),
            "ssl": cfg.ssl,
            "path": cfg.path,
            "auth": cfg.auth,
            "cert_validation": cfg.cert_validation,
            "connection_timeout": cfg.connect_timeout,
            "read_timeout": cfg.read_timeout,
            "operation_timeout": cfg.operation_timeout,
            "encryption": cfg.encryption,
        }
        if cfg.certificate_pem:
            kwargs["certificate_pem"] = cfg.certificate_pem
        if cfg.certificate_key_pem:
            kwargs["certificate_key_pem"] = cfg.certificate_key_pem
        certificate_password = cfg.resolved_certificate_key_password()
        if certificate_password is not None:
            kwargs["certificate_key_password"] = certificate_password
        return {key: value for key, value in kwargs.items() if value is not None}

    async def execute(
        self,
        command: str,
        *,
        shell: str = "auto",
        timeout: float = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RemoteResult:
        return await asyncio.wait_for(
            asyncio.to_thread(self._execute_sync, command, shell, cwd, env),
            timeout=timeout,
        )

    def _execute_sync(
        self,
        command: str,
        shell: str,
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> RemoteResult:
        started = time.monotonic()
        selected = "powershell" if shell in {"auto", "powershell", "pwsh"} else "cmd"
        with Client(self.config.host, **self._kwargs()) as client:
            if selected == "cmd":
                rendered = _prepare_cmd(command, cwd=cwd, env=env)
                stdout, stderr, rc = client.execute_cmd(rendered)
                return RemoteResult(
                    protocol="winrm",
                    host=self.config.host,
                    command=command,
                    stdout=_to_text(stdout),
                    stderr=_to_text(stderr),
                    exit_code=int(rc),
                    duration_seconds=round(time.monotonic() - started, 3),
                    ok=int(rc) == 0,
                )

            rendered = _prepare_powershell(command, cwd=cwd, env=env)
            output, streams, had_errors = client.execute_ps(rendered)
            stdout, marker_code = _parse_ps_output(output)
            stderr = _format_ps_streams(streams)
            code = marker_code if marker_code is not None else (1 if had_errors else 0)
            return RemoteResult(
                protocol="winrm",
                host=self.config.host,
                command=command,
                stdout=stdout,
                stderr=stderr,
                exit_code=code,
                duration_seconds=round(time.monotonic() - started, 3),
                ok=code == 0 and not had_errors,
            )

    async def upload(self, local_path: str, remote_path: str, *, recursive: bool = False) -> dict[str, Any]:
        return await asyncio.to_thread(self._upload_sync, local_path, remote_path, recursive)

    def _upload_sync(self, local_path: str, remote_path: str, recursive: bool) -> dict[str, Any]:
        source = Path(local_path)
        with Client(self.config.host, **self._kwargs()) as client:
            if source.is_dir():
                if not recursive:
                    raise IsADirectoryError("set recursive=true to upload a directory over WinRM")
                with tempfile.TemporaryDirectory(prefix="ssh-winrm-mcp-") as temp_dir:
                    archive_base = Path(temp_dir) / "payload"
                    archive = shutil.make_archive(str(archive_base), "zip", root_dir=str(source))
                    remote_zip = self._remote_temp_file(client, ".zip")
                    client.copy(archive, remote_zip)
                    script = (
                        f"New-Item -ItemType Directory -Force -Path {_ps_quote(remote_path)} | Out-Null; "
                        f"Expand-Archive -LiteralPath {_ps_quote(remote_zip)} "
                        f"-DestinationPath {_ps_quote(remote_path)} -Force; "
                        f"Remove-Item -LiteralPath {_ps_quote(remote_zip)} -Force"
                    )
                    _, streams, had_errors = client.execute_ps(script)
                    if had_errors:
                        raise RuntimeError(_format_ps_streams(streams) or "failed to expand uploaded directory")
            else:
                client.copy(str(source), remote_path)

        return {
            "ok": True,
            "protocol": "winrm",
            "host": self.config.host,
            "direction": "upload",
            "local_path": local_path,
            "remote_path": remote_path,
            "recursive": recursive,
        }

    async def download(self, remote_path: str, local_path: str, *, recursive: bool = False) -> dict[str, Any]:
        return await asyncio.to_thread(self._download_sync, remote_path, local_path, recursive)

    def _download_sync(self, remote_path: str, local_path: str, recursive: bool) -> dict[str, Any]:
        destination = Path(local_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with Client(self.config.host, **self._kwargs()) as client:
            if recursive:
                with tempfile.TemporaryDirectory(prefix="ssh-winrm-mcp-") as temp_dir:
                    local_zip = Path(temp_dir) / "payload.zip"
                    remote_zip = self._remote_temp_file(client, ".zip")
                    script = (
                        f"Compress-Archive -LiteralPath {_ps_quote(remote_path)} "
                        f"-DestinationPath {_ps_quote(remote_zip)} -Force"
                    )
                    _, streams, had_errors = client.execute_ps(script)
                    if had_errors:
                        raise RuntimeError(_format_ps_streams(streams) or "failed to archive remote directory")
                    try:
                        client.fetch(remote_zip, str(local_zip))
                    finally:
                        client.execute_ps(f"Remove-Item -LiteralPath {_ps_quote(remote_zip)} -Force -ErrorAction SilentlyContinue")
                    destination.mkdir(parents=True, exist_ok=True)
                    _safe_extract(local_zip, destination)
            else:
                client.fetch(remote_path, str(destination))

        return {
            "ok": True,
            "protocol": "winrm",
            "host": self.config.host,
            "direction": "download",
            "remote_path": remote_path,
            "local_path": local_path,
            "recursive": recursive,
        }

    def _remote_temp_file(self, client: Client, suffix: str) -> str:
        if self.config.remote_temp:
            temp_dir = self.config.remote_temp
        else:
            output, streams, had_errors = client.execute_ps("Write-Output $env:TEMP")
            if had_errors:
                raise RuntimeError(_format_ps_streams(streams) or "could not determine remote TEMP directory")
            temp_dir = _to_text(output).strip().splitlines()[-1]
        return temp_dir.rstrip("\\/") + "\\ssh-winrm-mcp-" + uuid.uuid4().hex + suffix

    async def open_session(self, *, shell: str | None = None) -> PersistentRemoteSession:
        if shell and shell.lower() not in {"powershell", "pwsh"}:
            raise ValueError("persistent WinRM sessions use a PowerShell runspace; shell must be powershell")
        session = WinRMPersistentSession(self.config, self._kwargs())
        await session.open()
        return session


class WinRMPersistentSession(PersistentRemoteSession):
    protocol = "winrm"

    def __init__(self, config: ConnectionConfig, kwargs: dict[str, Any]) -> None:
        self.host = config.host
        self._config = config
        self._kwargs = kwargs
        self._wsman: WSMan | None = None
        self._pool: RunspacePool | None = None
        self._active_ps: PowerShell | None = None
        self._active_lock = threading.RLock()
        self._execute_lock = asyncio.Lock()
        self._closed = False

    async def open(self) -> None:
        await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> None:
        self._wsman = WSMan(self.host, **self._kwargs)
        enter = getattr(self._wsman, "__enter__", None)
        if callable(enter):
            enter()
        self._pool = RunspacePool(
            self._wsman,
            configuration_name=self._config.configuration_name,
            min_runspaces=1,
            max_runspaces=1,
        )
        self._pool.open()

    async def execute(self, command: str, *, timeout: float) -> RemoteResult:
        async with self._execute_lock:
            if not self.alive():
                raise RuntimeError("WinRM session is closed")
            try:
                return await asyncio.wait_for(asyncio.to_thread(self._execute_sync, command), timeout=timeout)
            except asyncio.TimeoutError as exc:
                await self.interrupt()
                raise TimeoutError(f"WinRM session command exceeded {timeout} seconds") from exc

    def _execute_sync(self, command: str) -> RemoteResult:
        if self._pool is None:
            raise RuntimeError("WinRM runspace is not open")
        started = time.monotonic()
        ps = PowerShell(self._pool)
        with self._active_lock:
            self._active_ps = ps
        try:
            ps.add_script(_prepare_powershell(command, cwd=None, env=None))
            output = ps.invoke()
            stdout, marker_code = _parse_ps_output(output)
            stderr = _format_ps_streams(ps.streams)
            code = marker_code if marker_code is not None else (1 if ps.had_errors else 0)
            return RemoteResult(
                protocol="winrm",
                host=self.host,
                command=command,
                stdout=stdout,
                stderr=stderr,
                exit_code=code,
                duration_seconds=round(time.monotonic() - started, 3),
                ok=code == 0 and not ps.had_errors,
            )
        finally:
            with self._active_lock:
                self._active_ps = None
            with contextlib.suppress(Exception):
                ps.close()

    async def interrupt(self) -> None:
        await asyncio.to_thread(self._interrupt_sync)

    def _interrupt_sync(self) -> None:
        with self._active_lock:
            active = self._active_ps
        if active is not None:
            stop = getattr(active, "stop", None)
            if callable(stop):
                with contextlib.suppress(Exception):
                    stop()
                return
        # Closing the pool is the deterministic fallback.
        self._close_sync()

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pool is not None:
            with contextlib.suppress(Exception):
                self._pool.close()
        if self._wsman is not None:
            exit_method = getattr(self._wsman, "__exit__", None)
            if callable(exit_method):
                with contextlib.suppress(Exception):
                    exit_method(None, None, None)
            close = getattr(self._wsman, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()

    def alive(self) -> bool:
        return not self._closed and self._pool is not None


def _prepare_cmd(command: str, *, cwd: str | None, env: dict[str, str] | None) -> str:
    parts: list[str] = []
    if cwd:
        parts.append(f'cd /d "{cwd.replace(chr(34), chr(34) * 2)}"')
    if env:
        for key, value in env.items():
            _validate_env_name(key)
            parts.append(f'set "{key}={str(value).replace(chr(34), chr(34) * 2)}"')
    parts.append(command)
    return " && ".join(parts)


def _prepare_powershell(command: str, *, cwd: str | None, env: dict[str, str] | None) -> str:
    prefix: list[str] = ["$global:LASTEXITCODE = $null"]
    if cwd:
        prefix.append(f"Set-Location -LiteralPath {_ps_quote(cwd)}")
    if env:
        for key, value in env.items():
            _validate_env_name(key)
            prefix.append(f"$env:{key} = {_ps_quote(str(value))}")
    suffix = [
        "$__ssh_winrm_mcp_code = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } elseif ($?) { 0 } else { 1 }",
        'Write-Output "__SSH_WINRM_MCP_EXIT_CODE__=$__ssh_winrm_mcp_code"',
    ]
    return "\n".join(prefix + [command] + suffix)


def _parse_ps_output(output: Any) -> tuple[str, int | None]:
    lines = _to_text(output).splitlines()
    code: int | None = None
    retained: list[str] = []
    for line in lines:
        if line.startswith("__SSH_WINRM_MCP_EXIT_CODE__="):
            with contextlib.suppress(ValueError):
                code = int(line.split("=", 1)[1].strip())
        else:
            retained.append(line)
    return "\n".join(retained), code


def _format_ps_streams(streams: Any) -> str:
    if streams is None:
        return ""
    chunks: list[str] = []
    for name in ("error", "warning", "verbose", "debug", "information"):
        values = getattr(streams, name, None)
        if not values:
            continue
        for value in values:
            chunks.append(f"[{name}] {value}")
    return "\n".join(chunks)


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value)
    return str(value)


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _safe_extract(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"unsafe path in remote archive: {member.filename}") from exc
        zipped.extractall(destination)


def _validate_env_name(name: str) -> None:
    import re

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"invalid environment variable name: {name!r}")
