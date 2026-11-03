from __future__ import annotations

import contextlib
import os
import shlex
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .backends import make_backend
from .models import ConnectionConfig, merge_connection
from .paths import checked_local_path
from .policy import Policy
from .profiles import ProfileStore


class RemoteOperations:
    def __init__(self, profiles: ProfileStore) -> None:
        self.profiles = profiles

    def resolve(self, profile: str | None, connection: dict[str, Any] | None) -> ConnectionConfig:
        policy = Policy.from_environment()
        policy.check_overrides(connection, has_profile=bool(profile))
        stored = self.profiles.get_raw(profile) if profile else None
        if stored is None and not connection:
            raise ValueError("provide either profile or connection settings")
        return policy.check(merge_connection(stored, connection))

    async def test(self, config: ConnectionConfig) -> dict[str, Any]:
        backend = make_backend(config)
        command = (
            "Write-Output 'SSH_WINRM_MCP_CONNECTION_OK'"
            if config.protocol == "winrm"
            else "printf 'SSH_WINRM_MCP_CONNECTION_OK\\n'"
        )
        result = await backend.execute(
            command, shell="powershell" if config.protocol == "winrm" else "sh", timeout=30
        )
        payload = result.to_dict()
        payload["connection_ok"] = result.ok and "SSH_WINRM_MCP_CONNECTION_OK" in result.stdout
        return payload

    async def execute(
        self,
        config: ConnectionConfig,
        command: str,
        *,
        shell: str,
        timeout: float,
        cwd: str | None,
        env: dict[str, str] | None,
        output_limit: int | None,
    ) -> dict[str, Any]:
        result = await make_backend(config).execute(
            command,
            shell=shell,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        return result.to_dict(output_limit=output_limit)

    async def upload(
        self,
        config: ConnectionConfig,
        local_path: str,
        remote_path: str,
        *,
        recursive: bool,
    ) -> dict[str, Any]:
        source = checked_local_path(local_path, must_exist=True)
        if source.is_dir() and not recursive:
            raise IsADirectoryError("set recursive=true to upload a directory")
        return await make_backend(config).upload(str(source), remote_path, recursive=recursive)

    async def download(
        self,
        config: ConnectionConfig,
        remote_path: str,
        local_path: str,
        *,
        recursive: bool,
    ) -> dict[str, Any]:
        destination = checked_local_path(local_path, must_exist=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        return await make_backend(config).download(remote_path, str(destination), recursive=recursive)

    async def run_script(
        self,
        config: ConnectionConfig,
        *,
        script: str | None,
        local_path: str | None,
        language: str,
        interpreter: str | None,
        args: list[str] | None,
        remote_path: str | None,
        cleanup: bool,
        timeout: float,
        output_limit: int | None,
    ) -> dict[str, Any]:
        if bool(script) == bool(local_path):
            raise ValueError("provide exactly one of script or local_path")

        source_path: Path | None = None
        script_text: str
        if local_path:
            source_path = checked_local_path(local_path, must_exist=True)
            if not source_path.is_file():
                raise ValueError("local_path must point to a file")
            script_text = ""  # Local files may be binary executables; no decoding is needed.
        else:
            script_text = script or ""

        selected = _detect_language(language, config.protocol, source_path)
        suffix = _language_suffix(selected)
        backend = make_backend(config)

        if remote_path is None:
            temp_dir = await _remote_temp_dir(config, backend)
            separator = "\\" if config.protocol == "winrm" else "/"
            remote_path = temp_dir.rstrip("\\/") + separator + f"ssh-winrm-mcp-{uuid.uuid4().hex}{suffix}"

        temporary_local: tempfile.NamedTemporaryFile[str] | None = None
        try:
            if source_path is None:
                temporary_local = tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    suffix=suffix,
                    prefix="ssh-winrm-mcp-",
                    delete=False,
                )
                temporary_local.write(script_text)
                temporary_local.flush()
                temporary_local.close()
                upload_path = temporary_local.name
            else:
                upload_path = str(source_path)

            await backend.upload(upload_path, remote_path, recursive=False)
            command, shell = _script_command(config.protocol, remote_path, selected, interpreter, args or [])
            result = await backend.execute(command, shell=shell, timeout=timeout)
            payload = result.to_dict(output_limit=output_limit)
            payload.update({"language": selected, "remote_script_path": remote_path, "cleaned_up": False})
        finally:
            cleanup_succeeded = False
            if cleanup and remote_path:
                cleanup_command = (
                    f"Remove-Item -LiteralPath {_ps_quote(remote_path)} -Force -ErrorAction SilentlyContinue"
                    if config.protocol == "winrm"
                    else f"rm -f -- {shlex.quote(remote_path)}"
                )
                try:
                    cleanup_result = await backend.execute(
                        cleanup_command,
                        shell="powershell" if config.protocol == "winrm" else "sh",
                        timeout=30,
                    )
                    cleanup_succeeded = cleanup_result.ok
                except Exception:
                    cleanup_succeeded = False
            if temporary_local is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temporary_local.name)

        payload["cleaned_up"] = cleanup_succeeded if cleanup else False
        return payload


async def _remote_temp_dir(config: ConnectionConfig, backend: Any) -> str:
    if config.remote_temp:
        return config.remote_temp
    if config.protocol == "ssh":
        return "/tmp"
    result = await backend.execute("Write-Output $env:TEMP", shell="powershell", timeout=30)
    if not result.ok or not result.stdout.strip():
        raise RuntimeError(f"could not determine remote TEMP directory: {result.stderr}")
    return result.stdout.strip().splitlines()[-1]


def _detect_language(language: str, protocol: str, source_path: Path | None) -> str:
    selected = language.lower().strip()
    if selected != "auto":
        return selected
    suffix = source_path.suffix.lower() if source_path else ""
    mapping = {
        ".ps1": "powershell",
        ".bat": "batch",
        ".cmd": "batch",
        ".py": "python",
        ".sh": "bash",
    }
    if suffix in mapping:
        return mapping[suffix]
    return "powershell" if protocol == "winrm" else "bash"


def _language_suffix(language: str) -> str:
    return {
        "powershell": ".ps1",
        "pwsh": ".ps1",
        "batch": ".cmd",
        "cmd": ".cmd",
        "python": ".py",
        "bash": ".sh",
        "sh": ".sh",
        "executable": ".bin",
    }.get(language, ".txt")


def _script_command(
    protocol: str,
    remote_path: str,
    language: str,
    interpreter: str | None,
    args: list[str],
) -> tuple[str, str]:
    if protocol == "ssh":
        quoted_args = " ".join(shlex.quote(str(arg)) for arg in args)
        path = shlex.quote(remote_path)
        selected_interpreter = interpreter
        if selected_interpreter is None:
            selected_interpreter = {
                "python": "python3",
                "bash": "bash",
                "sh": "sh",
                "powershell": "pwsh",
                "pwsh": "pwsh",
                "executable": "",
            }.get(language, language)
        if language == "executable" or selected_interpreter == "":
            command = f"chmod +x -- {path} && {path} {quoted_args}".strip()
        else:
            command = f"{shlex.quote(selected_interpreter)} {path} {quoted_args}".strip()
        return command, "sh"

    quoted_args = " ".join(_ps_quote(str(arg)) for arg in args)
    path = _ps_quote(remote_path)
    if language in {"powershell", "pwsh"}:
        return f"& {path} {quoted_args}".strip(), "powershell"
    if language in {"batch", "cmd", "executable"}:
        return f"& {path} {quoted_args}".strip(), "powershell"
    if language == "python":
        executable = interpreter or "python.exe"
        return f"& {_ps_quote(executable)} {path} {quoted_args}".strip(), "powershell"
    executable = interpreter or language
    return f"& {_ps_quote(executable)} {path} {quoted_args}".strip(), "powershell"


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"
