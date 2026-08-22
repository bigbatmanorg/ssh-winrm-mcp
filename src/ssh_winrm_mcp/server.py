from __future__ import annotations

import os
import shlex
from typing import Any

from mcp.server.fastmcp import FastMCP

from .commands.registry import CommandRegistry
from .operations import RemoteOperations
from .profiles import ProfileStore, has_inline_secret
from .sessions import JobManager, SessionManager

profiles = ProfileStore()
operations = RemoteOperations(profiles)
sessions = SessionManager()
jobs = JobManager(sessions)
command_registry = CommandRegistry()

mcp = FastMCP(
    "SSH WinRM Remote",
    instructions=(
        "Remote administration over SSH and WinRM. Use stored profiles or pass an ad-hoc connection object. "
        "Named sessions preserve remote shell/runspace state. Long commands can run as asynchronous jobs."
    ),
    host=os.getenv("SSH_WINRM_MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("SSH_WINRM_MCP_PORT", "8765")),
    json_response=True,
)


@mcp.tool()
def remote_inventory_status() -> dict[str, Any]:
    """Show the automatic local inventory path and whether it already exists."""
    return profiles.status()


@mcp.tool()
def remote_inventory_init(overwrite: bool = False) -> dict[str, Any]:
    """Create an empty local inventory. A config path is not required to start the server."""
    return profiles.initialize(overwrite=overwrite)


@mcp.tool()
def remote_inventory_validate() -> dict[str, Any]:
    """Validate the inventory JSON and every stored SSH/WinRM profile."""
    return profiles.validate()


@mcp.tool()
def remote_profile_list(protocol: str | None = None, tag: str | None = None) -> dict[str, Any]:
    """List saved profiles with secrets redacted; optionally filter by protocol or tag."""
    return {"config_file": str(profiles.path), "profiles": profiles.list_safe(protocol=protocol, tag=tag)}


@mcp.tool()
def remote_profile_get(name: str) -> dict[str, Any]:
    """Read one saved connection profile with secrets redacted."""
    return {"name": name, "settings": profiles.get(name).to_dict(redact=True)}


@mcp.tool()
def remote_profile_create(name: str, settings: dict[str, Any]) -> dict[str, Any]:
    """Create a saved profile. Prefer password_env and passphrase environment references."""
    config = profiles.create(name, settings)
    return {
        "ok": True,
        "name": name,
        "settings": config.to_dict(redact=True),
        "warning": "an inline secret was stored in the protected local JSON inventory"
        if has_inline_secret(settings)
        else None,
    }


@mcp.tool()
def remote_profile_update(
    name: str,
    changes: dict[str, Any],
    remove_fields: list[str] | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Update a profile by merging changes, or fully replace it with replace=true.

    Use remove_fields to delete optional settings. Required fields must remain valid.
    """
    config = profiles.update(name, changes, remove_fields=remove_fields, replace=replace)
    return {
        "ok": True,
        "name": name,
        "settings": config.to_dict(redact=True),
        "warning": "an inline secret was stored in the protected local JSON inventory"
        if has_inline_secret(changes)
        else None,
    }


@mcp.tool()
def remote_profile_rename(name: str, new_name: str, overwrite: bool = False) -> dict[str, Any]:
    """Rename a saved profile."""
    return {"ok": profiles.rename(name, new_name, overwrite=overwrite), "name": name, "new_name": new_name}


@mcp.tool()
def remote_profile_delete(name: str) -> dict[str, Any]:
    """Delete a saved connection profile."""
    return {"ok": profiles.delete(name), "name": name}


@mcp.tool()
async def remote_test(
    profile: str | None = None,
    connection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Test a saved or ad-hoc connection by executing a harmless marker command.

    For WinRM HTTPS on port 5986, keep ssl=true. If the server uses a self-signed
    certificate, also set cert_validation=false; ssl=false selects plain HTTP
    instead of disabling certificate verification.
    """
    config = operations.resolve(profile, connection)
    return await operations.test(config)


@mcp.tool()
async def remote_execute(
    command: str,
    profile: str | None = None,
    connection: dict[str, Any] | None = None,
    shell: str = "auto",
    timeout: float = 300,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    output_limit: int | None = None,
) -> dict[str, Any]:
    """Execute one command using a short-lived SSH or WinRM connection.

    connection may override a profile or fully describe an ad-hoc target, for example:
    {"protocol":"ssh","host":"server","username":"root","password":"..."}.
    For self-signed WinRM HTTPS use ssl=true and cert_validation=false.
    WinRM shell may be powershell or cmd. SSH executes through the remote user's shell.
    """
    config = operations.resolve(profile, connection)
    return await operations.execute(
        config,
        command,
        shell=shell,
        timeout=timeout,
        cwd=cwd,
        env=env,
        output_limit=output_limit,
    )


@mcp.tool()
async def remote_run_program(
    program: str,
    args: list[str] | None = None,
    profile: str | None = None,
    connection: dict[str, Any] | None = None,
    timeout: float = 600,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    output_limit: int | None = None,
) -> dict[str, Any]:
    """Run an executable with a safely quoted argument list on a saved or ad-hoc target."""
    config = operations.resolve(profile, connection)
    values = args or []
    if config.protocol == "ssh":
        command = " ".join([shlex.quote(program), *(shlex.quote(str(value)) for value in values)])
        shell = "sh"
    else:
        ps_quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        command = "& " + " ".join([ps_quote(program), *(ps_quote(value) for value in values)])
        shell = "powershell"
    return await operations.execute(
        config,
        command,
        shell=shell,
        timeout=timeout,
        cwd=cwd,
        env=env,
        output_limit=output_limit,
    )


@mcp.tool()
async def remote_run_script(
    profile: str | None = None,
    connection: dict[str, Any] | None = None,
    script: str | None = None,
    local_path: str | None = None,
    language: str = "auto",
    interpreter: str | None = None,
    args: list[str] | None = None,
    remote_path: str | None = None,
    cleanup: bool = True,
    timeout: float = 600,
    output_limit: int | None = None,
) -> dict[str, Any]:
    """Upload and run an inline or local script. Supports bash/sh, PowerShell, batch, Python, and executables."""
    config = operations.resolve(profile, connection)
    return await operations.run_script(
        config,
        script=script,
        local_path=local_path,
        language=language,
        interpreter=interpreter,
        args=args,
        remote_path=remote_path,
        cleanup=cleanup,
        timeout=timeout,
        output_limit=output_limit,
    )


@mcp.tool()
async def remote_upload(
    local_path: str,
    remote_path: str,
    profile: str | None = None,
    connection: dict[str, Any] | None = None,
    recursive: bool = False,
) -> dict[str, Any]:
    """Copy a local file or directory to a remote host. Set recursive=true for directories."""
    config = operations.resolve(profile, connection)
    return await operations.upload(config, local_path, remote_path, recursive=recursive)


@mcp.tool()
async def remote_download(
    remote_path: str,
    local_path: str,
    profile: str | None = None,
    connection: dict[str, Any] | None = None,
    recursive: bool = False,
) -> dict[str, Any]:
    """Copy a remote file or directory to the MCP server. Set recursive=true for directories."""
    config = operations.resolve(profile, connection)
    return await operations.download(config, remote_path, local_path, recursive=recursive)


@mcp.tool()
async def remote_session_open(
    name: str,
    profile: str | None = None,
    connection: dict[str, Any] | None = None,
    shell: str | None = None,
) -> dict[str, Any]:
    """Open a named persistent SSH shell or WinRM PowerShell runspace.

    Sessions remain alive until closed or until the MCP server process exits. SSH shell state such as cwd and
    exported variables persists. WinRM uses one reusable PSRP runspace.
    """
    config = operations.resolve(profile, connection)
    return await sessions.open(name, config, shell=shell)


@mcp.tool()
def remote_session_list() -> dict[str, Any]:
    """List active named sessions and their health."""
    return {"sessions": sessions.list()}


@mcp.tool()
async def remote_session_execute(
    name: str,
    command: str,
    timeout: float = 3600,
    output_limit: int | None = None,
) -> dict[str, Any]:
    """Run a command inside a named persistent session and wait for completion."""
    return (await sessions.execute(name, command, timeout=timeout)).to_dict(output_limit=output_limit)


@mcp.tool()
async def remote_session_close(name: str) -> dict[str, Any]:
    """Close one named session and its remote shell/runspace."""
    return {"ok": await sessions.close(name), "name": name}


@mcp.tool()
async def remote_session_close_all() -> dict[str, Any]:
    """Close every active session."""
    return {"ok": True, "closed": await sessions.close_all()}


@mcp.tool()
async def remote_session_cleanup_idle(max_idle_seconds: float = 3600) -> dict[str, Any]:
    """Close named sessions which have been idle for at least the requested number of seconds."""
    closed = await sessions.cleanup_idle(max_idle_seconds)
    return {"ok": True, "closed": closed, "count": len(closed), "max_idle_seconds": max_idle_seconds}


@mcp.tool()
def remote_job_start(session_name: str, command: str, timeout: float = 86400) -> dict[str, Any]:
    """Start a long-running command in a named session and return immediately with a job ID."""
    return jobs.start(session_name, command, timeout=timeout)


@mcp.tool()
def remote_job_list(session_name: str | None = None) -> dict[str, Any]:
    """List asynchronous jobs, optionally restricted to one session."""
    return {"jobs": jobs.list(session_name)}


@mcp.tool()
def remote_job_status(job_id: str) -> dict[str, Any]:
    """Get the current state of a long-running job."""
    return jobs.get(job_id).summary()


@mcp.tool()
async def remote_job_result(job_id: str, wait: bool = False, timeout: float = 0) -> dict[str, Any]:
    """Get a job result. Set wait=true to wait, optionally bounded by timeout seconds."""
    return await jobs.result(job_id, wait=wait, timeout=timeout)


@mcp.tool()
async def remote_job_cancel(job_id: str) -> dict[str, Any]:
    """Interrupt and cancel a running job. SSH cancellation closes that named shell session."""
    return await jobs.cancel(job_id)


@mcp.tool()
def remote_command_groups(reload: bool = False, create_directory: bool = False) -> dict[str, Any]:
    """List command groups and the persistent local plug-in directory.

    Set create_directory=true once to create the folder. Set reload=true after adding or editing a module.
    """
    if reload:
        command_registry.reload()
    return command_registry.status(create_directory=create_directory)


@mcp.tool()
def remote_command_group_describe(group: str) -> dict[str, Any]:
    """Describe the actions and parameters available in a specialized command group."""
    return command_registry.describe(group)


@mcp.tool()
async def remote_command_group_run(
    group: str,
    action: str,
    parameters: dict[str, Any] | None = None,
    profile: str | None = None,
    connection: dict[str, Any] | None = None,
    session_name: str | None = None,
    dry_run: bool = False,
    timeout: float = 300,
    output_limit: int | None = None,
) -> dict[str, Any]:
    """Build and run a specialized action, such as user management, services, reboot, or system inventory.

    Use either session_name or profile/connection. dry_run returns the generated command without executing it.
    """
    if session_name:
        record = sessions.get(session_name)
        protocol = record.config.protocol
    else:
        config = operations.resolve(profile, connection)
        protocol = config.protocol

    plan = command_registry.build(group, action, protocol, parameters)
    visible_command = plan.redacted_command or plan.command
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "group": group,
            "action": action,
            "summary": plan.summary,
            "dangerous": plan.dangerous,
            "command": visible_command,
            "shell": plan.shell,
        }

    if session_name:
        result = await sessions.execute(session_name, plan.command, timeout=timeout)
        payload = result.to_dict(output_limit=output_limit)
    else:
        payload = await operations.execute(
            config,
            plan.command,
            shell=plan.shell,
            timeout=timeout,
            cwd=None,
            env=None,
            output_limit=output_limit,
        )

    payload.update(
        {
            "group": group,
            "action": action,
            "summary": plan.summary,
            "dangerous": plan.dangerous,
            "command": visible_command,
        }
    )
    return payload


@mcp.resource("ssh-winrm://inventory/profiles")
def profiles_resource() -> str:
    """Saved profile names and redacted settings."""
    import json

    return json.dumps({"status": profiles.status(), "profiles": profiles.list_safe()}, indent=2)


@mcp.resource("ssh-winrm://command-groups")
def command_groups_resource() -> str:
    """Available specialized command groups."""
    import json

    return json.dumps(command_registry.status(), indent=2)


def main() -> None:
    transport = os.getenv("SSH_WINRM_MCP_TRANSPORT", "stdio").strip().lower()
    if transport not in {"stdio", "streamable-http", "sse"}:
        raise SystemExit("SSH_WINRM_MCP_TRANSPORT must be stdio, streamable-http, or sse")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
