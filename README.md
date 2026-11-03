# ssh-winrm-mcp

A small, pure-Python MCP server for administering Linux/Unix hosts over SSH and Windows hosts over WinRM/PowerShell Remoting.

It is designed to run directly from GitHub with `uvx`. No repository checkout and no config path are required. The server starts with an empty inventory and can create and maintain its own persistent local inventory through MCP tools.

## Features

- Stored connection profiles or completely ad-hoc connection settings.
- SSH password, private key, SSH agent, host-key verification, commands, programs, scripts, SFTP files, and recursive directories.
- WinRM/PSRP with NTLM, Basic, Negotiate, Kerberos, CredSSP, or certificate settings supported by `pypsrp`.
- PowerShell and CMD execution, script/program execution, and file or directory transfer.
- Named persistent SSH shells and WinRM PowerShell runspaces.
- Long-running asynchronous jobs inside named sessions.
- Persistent local inventory with initialization, validation, create, read, update, rename, delete, tags, and atomic writes.
- Built-in command groups for system information, services, restart/shutdown, and local user management.
- Persistent external command-group folder for specialized modules.
- `stdio`, Streamable HTTP, and SSE transports.

## Run directly from GitHub

Pinned to a release (recommended):

```bash
uvx --from git+https://github.com/bigbatmanorg/ssh-winrm-mcp.git@v0.3.0 ssh-winrm-mcp
```

Releases and changes: [releases page](https://github.com/bigbatmanorg/ssh-winrm-mcp/releases), [CHANGELOG.md](CHANGELOG.md).

The default transport is `stdio`, suitable for a local MCP client.

### VS Code MCP configuration

```json
{
  "servers": {
    "ssh-winrm": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/bigbatmanorg/ssh-winrm-mcp.git@v0.3.0",
        "ssh-winrm-mcp"
      ]
    }
  }
}
```

No inventory environment variable is required.

## Local development

```bash
git clone https://github.com/bigbatmanorg/ssh-winrm-mcp.git
cd ssh-winrm-mcp
uv sync --extra dev
uv run pytest
uv run ssh-winrm-mcp
```

## Automatic local storage

When `SSH_WINRM_MCP_CONFIG` is not set, the inventory is stored here:

- Linux/macOS: `${XDG_CONFIG_HOME:-~/.config}/ssh-winrm-mcp/inventory.json`
- Windows: `%APPDATA%\ssh-winrm-mcp\inventory.json`

The file does not need to exist before startup. The first profile create operation also creates it automatically. Writes are atomic and the file is restricted to the current user where the operating system supports Unix permissions.

A custom path remains possible:

```bash
SSH_WINRM_MCP_CONFIG=/srv/private/remote-inventory.json \
uvx --from git+https://github.com/bigbatmanorg/ssh-winrm-mcp.git@v0.3.0 ssh-winrm-mcp
```

## Inventory tools

- `remote_inventory_status`
- `remote_inventory_init`
- `remote_inventory_validate`
- `remote_profile_list`
- `remote_profile_get`
- `remote_profile_create`
- `remote_profile_update`
- `remote_profile_rename`
- `remote_profile_delete`

An agent can start with no file and create a profile itself:

```text
remote_inventory_init()

remote_profile_create(
  name="linux01",
  settings={
    "protocol": "ssh",
    "host": "linux01.home",
    "username": "admin",
    "private_key_path": "~/.ssh/id_ed25519",
    "verify_host_key": true,
    "tags": ["linux", "lab"]
  }
)
```

Update only selected fields:

```text
remote_profile_update(
  name="linux01",
  changes={"host": "192.168.1.20", "tags": ["linux", "production"]}
)
```

Remove optional fields:

```text
remote_profile_update(
  name="linux01",
  changes={},
  remove_fields=["password", "password_env"]
)
```

Profiles may store inline credentials, but that means plaintext secrets are present in the protected JSON file. Prefer `password_env`, `private_key_passphrase_env`, and `certificate_key_password_env` where practical.

See `inventory.example.json` for SSH and WinRM examples.

## Ad-hoc use

Every one-shot operation and `remote_session_open` accepts a full `connection` object without saving it:

```json
{
  "protocol": "ssh",
  "host": "192.168.1.50",
  "username": "root",
  "password": "temporary-password",
  "verify_host_key": false
}
```

A connection object can also override selected fields from a saved profile.

### WinRM HTTPS and self-signed certificates

`ssl` selects the transport; it does not control certificate verification. For
the standard HTTPS listener on port 5986, use both `"ssl": true` and
`"cert_validation": false` when the server has a self-signed certificate:

```json
{
  "protocol": "winrm",
  "host": "192.168.1.50",
  "port": 5986,
  "username": "Administrator",
  "password_env": "WINRM_PASSWORD",
  "auth": "ntlm",
  "ssl": true,
  "cert_validation": false
}
```

For clients that need a process-wide default, set
`WINRM_DISABLE_SSL_CERT_CHECK=1` in the MCP server environment. An explicit
`cert_validation` value in a profile or ad-hoc connection takes precedence.
Disabling validation should only be used for trusted hosts; installing a
certificate issued by a trusted CA is preferable.

## Security model

The server runs commands with the remote account's full rights, and its arguments come from an AI agent that can be steered by untrusted content (web pages, files, command output). The operator limits what the agent can reach:

| Setting | Default | Effect |
|---|---|---|
| `SSH_WINRM_MCP_ALLOWED_HOSTS` | any | Comma-separated glob patterns (`*.lab.example,10.0.110.*`); connections to other hosts are refused, including saved profiles. |
| `SSH_WINRM_MCP_ALLOW_ADHOC` | `true` | `false` allows only saved profiles; per-call `connection` may then only change timeouts. |
| `SSH_WINRM_MCP_SECRET_ENV_PATTERNS` | `*PASSWORD*,*PASSWD*,*_PASS,*PASSPHRASE*` | `password_env` and the other `*_env` fields may only read variables matching these globs, so an agent cannot send e.g. `AWS_SECRET_ACCESS_KEY` to a host as a password. |
| `SSH_WINRM_MCP_ALLOW_INSECURE_BASIC` | `false` | WinRM basic auth over plain HTTP (cleartext password) is refused unless enabled. |
| `SSH_WINRM_MCP_LOCAL_ROOT` | `${XDG_DATA_HOME:-~/.local/share}/ssh-winrm-mcp/files` | Uploads, downloads and local scripts are confined here; relative paths are relative to it. Set `/` to allow any path. The server's config directory (inventory and command plug-ins) is always refused, so a download can never plant a plug-in. |

`remote_policy` shows the active policy. Every tool carries MCP annotations (`readOnlyHint`, `destructiveHint`, …) so clients can ask before running remote commands. Do not expose HTTP mode to untrusted networks.

## Main remote tools

### One-shot operations

- `remote_test`
- `remote_execute`
- `remote_run_program`
- `remote_run_script`
- `remote_upload`
- `remote_download`

Example:

```text
remote_execute(profile="linux01", command="uname -a")
```

```text
remote_run_script(
  profile="win01",
  language="powershell",
  script="Get-Service | Select-Object -First 10"
)
```

## Persistent sessions

- `remote_session_open`
- `remote_session_list`
- `remote_session_execute`
- `remote_session_close`
- `remote_session_close_all`
- `remote_session_cleanup_idle`

SSH uses a real reusable remote shell, so `cd`, shell variables, and exported environment state persist:

```text
remote_session_open(name="linux-maint", profile="linux01", shell="bash")
remote_session_execute(name="linux-maint", command="cd /opt/myapp")
remote_session_execute(name="linux-maint", command="export RELEASE=2026.07")
remote_session_execute(name="linux-maint", command="pwd; echo $RELEASE")
```

WinRM uses one reusable PSRP PowerShell runspace. Use global variables when state must survive separate PowerShell pipelines:

```text
remote_session_open(name="win-maint", profile="win01", shell="powershell")
remote_session_execute(name="win-maint", command="$global:Target='C:\\Apps'")
remote_session_execute(name="win-maint", command="Get-ChildItem $global:Target")
```

Sessions are memory-only. Restarting the MCP process closes them. Commands in one session are serialized; create several named sessions for parallel work.

SSH sessions need a POSIX shell (sh, bash, zsh) and are non-interactive: each command's stdin is `/dev/null`, so commands that read input (`cat`, `read`, prompts) get end-of-file instead of hanging. `exit` ends the session. For Windows hosts use a WinRM session; one-shot `remote_execute` over SSH works with Windows OpenSSH.

## Long-running jobs

- `remote_job_start`
- `remote_job_list`
- `remote_job_status`
- `remote_job_result`
- `remote_job_cancel`

```text
remote_job_start(
  session_name="linux-maint",
  command="./upgrade.sh",
  timeout=86400
)
```

The call returns a job ID immediately. Poll with `remote_job_status`, then retrieve the result with `remote_job_result`. Current job output is returned when the command finishes; it is not incrementally streamed while running.

## Command groups

Built-in groups currently include (Unix actions use `sudo -n`, so they fail fast instead of waiting for a password prompt when passwordless sudo is not configured):

- `system`: information, disk usage, processes, services, reboot, shutdown.
- `users`: list, create, delete, enable, disable, and group membership.

Use:

- `remote_command_groups`
- `remote_command_group_describe`
- `remote_command_group_run`

The persistent plug-in directory defaults beside the inventory:

```text
~/.config/ssh-winrm-mcp/commands/
```

On Windows it is under `%APPDATA%\ssh-winrm-mcp\commands\`.

Create it through MCP:

```text
remote_command_groups(create_directory=true)
```

Place Python modules there and call `remote_command_groups(reload=true)`. The `examples/custom_maintenance.py` file is a starting point. A module exports `GROUP_NAME` and an `ACTIONS` dictionary containing `CommandAction` objects.

A custom location may be selected with `SSH_WINRM_MCP_COMMANDS_DIR`.

## HTTP mode

```bash
SSH_WINRM_MCP_TRANSPORT=streamable-http \
SSH_WINRM_MCP_HOST=127.0.0.1 \
SSH_WINRM_MCP_PORT=8765 \
uvx --from git+https://github.com/bigbatmanorg/ssh-winrm-mcp.git@v0.3.0 ssh-winrm-mcp
```

Endpoint:

```text
http://127.0.0.1:8765/mcp
```

Do not expose this server to an untrusted network. It intentionally provides remote command execution and credential-handling capabilities.

## Optional environment variables

- `SSH_WINRM_MCP_CONFIG`
- `SSH_WINRM_MCP_TRANSPORT`
- `SSH_WINRM_MCP_HOST`
- `SSH_WINRM_MCP_PORT`
- `SSH_WINRM_MCP_LOCAL_ROOT`
- `SSH_WINRM_MCP_ALLOWED_HOSTS`, `SSH_WINRM_MCP_ALLOW_ADHOC`, `SSH_WINRM_MCP_SECRET_ENV_PATTERNS`, `SSH_WINRM_MCP_ALLOW_INSECURE_BASIC` (see Security model)
- `SSH_WINRM_MCP_MAX_OUTPUT_CHARS`
- `SSH_WINRM_MCP_COMMANDS_DIR`

## Testing

```bash
uv sync --extra dev
uv run pytest                      # offline unit tests
REMOTE_LIVE=1 uv run pytest tests/live -v   # live tests over MCP stdio
```

Live tests read `SSH_TEST_*` (a disposable sshd container works well; the tests create and delete a local user there) and optionally `WINRM_TEST_*` for a Windows host; see `tests/live/test_live_remote.py`.

## Project layout

```text
ssh-winrm-mcp/
├── pyproject.toml
├── inventory.example.json
├── src/ssh_winrm_mcp/
│   ├── server.py
│   ├── operations.py
│   ├── sessions.py
│   ├── profiles.py
│   ├── backends/
│   └── commands/
├── examples/
└── tests/
```
