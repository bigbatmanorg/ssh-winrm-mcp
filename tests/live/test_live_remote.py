"""Live end-to-end tests through the real MCP stdio server.

Opt-in with REMOTE_LIVE=1. SSH tests need SSH_TEST_HOST, SSH_TEST_PORT, SSH_TEST_USER (passwordless
sudo), SSH_TEST_NOSUDO_USER, SSH_TEST_KEY and SSH_TEST_PASSWORD (a disposable sshd container is
ideal; the tests create and delete a local user there). WinRM tests additionally need
WINRM_TEST_HOST, WINRM_TEST_ADMIN_USER and WINRM_TEST_ADMIN_PASSWORD and skip when the host is
unreachable.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.skipif(os.getenv("REMOTE_LIVE") != "1", reason="set REMOTE_LIVE=1 for live tests")


def ssh(user: str | None = None, **extra):
    return {
        "protocol": "ssh",
        "host": os.getenv("SSH_TEST_HOST", "127.0.0.1"),
        "port": int(os.getenv("SSH_TEST_PORT", "22")),
        "username": user or os.getenv("SSH_TEST_USER"),
        "private_key_path": os.getenv("SSH_TEST_KEY"),
        "verify_host_key": False,
        **extra,
    }


def winrm(**extra):
    return {
        "protocol": "winrm",
        "host": os.getenv("WINRM_TEST_HOST", ""),
        "username": os.getenv("WINRM_TEST_ADMIN_USER"),
        "password_env": "WINRM_TEST_ADMIN_PASSWORD",
        "auth": "ntlm",
        "ssl": True,
        "cert_validation": False,
        **extra,
    }


def reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except OSError:
        return False


windows = pytest.mark.skipif(
    not reachable(os.getenv("WINRM_TEST_HOST", "invalid.invalid"), 5986), reason="WinRM test host unreachable"
)


@asynccontextmanager
async def server(tmp_path: Path, **env: str):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ssh_winrm_mcp"],
        env={
            **os.environ,
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            **env,
        },
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


async def call(session: ClientSession, tool: str, **arguments):
    result = await session.call_tool(tool, arguments)
    text = result.content[0].text if result.content else ""
    if result.isError:
        return {"__error__": text}
    return json.loads(text)


async def test_ssh_auth_exec_exit_codes_env_and_cwd(tmp_path):
    async with server(tmp_path) as s:
        assert (await call(s, "remote_test", connection=ssh()))["connection_ok"]
        password = ssh(private_key_path=None, ssh_agent=False, password_env="SSH_TEST_PASSWORD")
        assert (await call(s, "remote_test", connection=password))["connection_ok"]
        result = await call(
            s,
            "remote_execute",
            connection=ssh(),
            command='echo "$MODE in $(pwd)"; echo oops >&2; exit 3',
            cwd="/tmp",
            env={"MODE": "live"},
        )
        assert (result["stdout"].strip(), result["stderr"].strip(), result["exit_code"]) == (
            "live in /tmp",
            "oops",
            3,
        )
        program = await call(
            s, "remote_run_program", connection=ssh(), program="printf", args=["%s|", "a b", "c'd"]
        )
        assert program["stdout"] == "a b|c'd|"
        script = await call(
            s,
            "remote_run_script",
            connection=ssh(),
            script="#!/bin/bash\necho script-$((6*7))",
            language="bash",
        )
        assert script["stdout"].strip() == "script-42" and script["cleaned_up"] is True


async def test_ssh_sessions_keep_state_and_never_hang_on_stdin(tmp_path):
    async with server(tmp_path) as s:
        await call(s, "remote_session_open", name="m", connection=ssh(), shell="bash")
        await call(s, "remote_session_execute", name="m", command="cd /tmp && export RELEASE=2026.11")
        state = await call(s, "remote_session_execute", name="m", command="pwd; echo $RELEASE")
        assert state["stdout"].split() == ["/tmp", "2026.11"]
        started = time.monotonic()
        cat = await call(
            s, "remote_session_execute", name="m", command="cat; read x; echo after-stdin", timeout=20
        )
        assert cat["stdout"].strip() == "after-stdin" and time.monotonic() - started < 10
        first = await call(
            s, "remote_session_execute", name="m", command="echo out; (sleep 0.5; echo late-err >&2) & wait"
        )
        second = await call(s, "remote_session_execute", name="m", command="echo next")
        assert first["stderr"].strip() == "late-err" and second["stderr"] == ""
        empty = await call(s, "remote_session_execute", name="m", command="   ")
        assert empty["exit_code"] == 0
        heredoc = await call(s, "remote_session_execute", name="m", command="cat <<'EOF'\nline1\nEOF")
        assert heredoc["stdout"].strip() == "line1"
        listed = await call(s, "remote_session_list")
        assert listed["sessions"][0]["alive"] is True
        job = await call(s, "remote_job_start", session_name="m", command="sleep 2; echo job-done")
        done = await call(s, "remote_job_result", job_id=job["job_id"], wait=True, timeout=30)
        assert done["status"] == "completed" and done["result"]["stdout"].strip() == "job-done"
        await call(s, "remote_session_close_all")


async def test_command_groups_use_non_interactive_sudo(tmp_path):
    name = f"mcp{uuid.uuid4().hex[:6]}"
    async with server(tmp_path) as s:
        started = time.monotonic()
        denied = await call(
            s,
            "remote_command_group_run",
            connection=ssh(user=os.getenv("SSH_TEST_NOSUDO_USER")),
            group="users",
            action="create",
            parameters={"username": name},
            timeout=30,
        )
        assert (
            denied.get("ok") is False
            and "password" in denied["stderr"].lower()
            and time.monotonic() - started < 15
        )
        created = await call(
            s,
            "remote_command_group_run",
            connection=ssh(),
            group="users",
            action="create",
            parameters={"username": name},
        )
        assert created["ok"], created
        listed = await call(s, "remote_command_group_run", connection=ssh(), group="users", action="list")
        assert name in listed["stdout"]
        deleted = await call(
            s,
            "remote_command_group_run",
            connection=ssh(),
            group="users",
            action="delete",
            parameters={"username": name, "remove_home": True},
        )
        assert deleted["ok"], deleted


async def test_file_transfer_is_sandboxed(tmp_path):
    root = tmp_path / "files"
    async with server(tmp_path, SSH_WINRM_MCP_LOCAL_ROOT=str(root)) as s:
        policy = await call(s, "remote_policy")
        assert policy["local_root"] == str(root.resolve())
        (root / "up").mkdir(parents=True)
        (root / "up" / "a.txt").write_text("payload")
        remote_dir = f"/tmp/mcp-{uuid.uuid4().hex}"
        up = await call(
            s, "remote_upload", connection=ssh(), local_path="up", remote_path=remote_dir, recursive=True
        )
        assert up["ok"]
        down = await call(
            s, "remote_download", connection=ssh(), remote_path=f"{remote_dir}/a.txt", local_path="back/a.txt"
        )
        assert down["ok"] and (root / "back" / "a.txt").read_text() == "payload"
        outside = await call(
            s,
            "remote_download",
            connection=ssh(),
            remote_path=f"{remote_dir}/a.txt",
            local_path="../../escape.txt",
        )
        assert "outside" in outside["__error__"]
        await call(s, "remote_execute", connection=ssh(), command=f"rm -rf {remote_dir}")
    async with server(tmp_path, SSH_WINRM_MCP_LOCAL_ROOT="/") as s:
        plugin = tmp_path / "config" / "ssh-winrm-mcp" / "commands" / "evil.py"
        blocked = await call(
            s, "remote_download", connection=ssh(), remote_path="/etc/hostname", local_path=str(plugin)
        )
        assert "configuration directory" in blocked["__error__"] and not plugin.exists()


async def test_policy_blocks_hosts_and_secret_exfiltration(tmp_path):
    async with server(tmp_path, SSH_WINRM_MCP_ALLOWED_HOSTS="lab-only.example") as s:
        blocked = await call(s, "remote_test", connection=ssh())
        assert "not allowed" in blocked["__error__"]
    async with server(tmp_path) as s:
        exfil = await call(s, "remote_test", connection=ssh(private_key_path=None, password_env="HOME"))
        assert "SECRET_ENV_PATTERNS" in exfil["__error__"]
        saved = await call(s, "remote_profile_create", name="x", settings=ssh(password_env="PATH"))
        assert "SECRET_ENV_PATTERNS" in saved["__error__"]


@windows
async def test_winrm_exit_codes_timeouts_env_and_sessions(tmp_path):
    proof = f"C:\\Windows\\Temp\\mcp-swm-{uuid.uuid4().hex}.txt"
    async with server(tmp_path) as s:
        assert (await call(s, "remote_test", connection=winrm()))["connection_ok"]
        exited = await call(s, "remote_execute", connection=winrm(), command="Write-Output a; exit 3")
        assert (exited["exit_code"], exited["ok"]) == (3, False)
        cmd = await call(
            s,
            "remote_execute",
            connection=winrm(),
            shell="cmd",
            command="echo %MCP_MODE%",
            env={"MCP_MODE": "ok"},
        )
        assert cmd["stdout"].strip() == "ok"
        timed_out = await call(
            s,
            "remote_execute",
            connection=winrm(),
            command=f"Start-Sleep 8; Set-Content {proof} x",
            timeout=3,
        )
        assert "timeout" in timed_out["__error__"].lower()
        time.sleep(9)
        check = await call(s, "remote_execute", connection=winrm(), command=f"Test-Path {proof}")
        assert check["stdout"].strip() == "False"
        await call(s, "remote_session_open", name="w", connection=winrm())
        await call(s, "remote_session_execute", name="w", command="$global:Target = 'kept'")
        kept = await call(
            s, "remote_session_execute", name="w", command="Write-Output $global:Target; exit 4"
        )
        assert kept["stdout"].strip() == "kept" and kept["exit_code"] == 4
        await call(s, "remote_session_close_all")


@windows
async def test_ssh_to_windows_one_shot_and_clear_session_error(tmp_path):
    host = os.getenv("WINRM_TEST_HOST", "")
    conn = {
        "protocol": "ssh",
        "host": host,
        "username": os.getenv("WINRM_TEST_ADMIN_USER"),
        "password_env": "WINRM_TEST_ADMIN_PASSWORD",
        "verify_host_key": False,
        "ssh_agent": False,
    }
    if not reachable(host, 22):
        pytest.skip("SSH on the Windows host is unreachable")
    async with server(tmp_path) as s:
        who = await call(s, "remote_execute", connection=conn, command="whoami")
        assert os.getenv("WINRM_TEST_ADMIN_USER", "").lower() in who["stdout"].lower()
        session = await call(s, "remote_session_open", name="win", connection=conn)
        assert "POSIX shell" in session["__error__"]
