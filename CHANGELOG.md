# Changelog

## 0.3.0 — 2026-11-03

### Security
- **Local file sandbox by default.** Uploads, downloads and local scripts are confined to `SSH_WINRM_MCP_LOCAL_ROOT`, which defaults to a private data directory; set `/` to allow any path. The server's own configuration directory is always refused. Previously a download could write anywhere, including the command plug-in directory, whose `.py` files run on `remote_command_groups(reload=true)`; that made local code execution possible.
- **Secret environment variables are restricted.** `password_env`, `private_key_passphrase_env` and `certificate_key_password_env` may only name variables that match `SSH_WINRM_MCP_SECRET_ENV_PATTERNS`. This stops an agent from sending arbitrary server environment variables, such as cloud keys, to a host it controls.
- **Host allowlist and ad-hoc switch.** `SSH_WINRM_MCP_ALLOWED_HOSTS` limits which hosts can be reached, and `SSH_WINRM_MCP_ALLOW_ADHOC=false` allows only saved profiles. Both are also enforced when profiles are created or updated.
- **No cleartext WinRM passwords.** WinRM basic auth over plain HTTP is refused unless `SSH_WINRM_MCP_ALLOW_INSECURE_BASIC=true`.
- **`remote_policy` tool** shows the active policy and file root.
- **MCP annotations** on every tool.

### Fixed
- **SSH sessions no longer hang on stdin.** Commands that read stdin (`cat`, `read`, prompts) used to consume the completion marker and hang until the timeout, which then killed the session. Each command now runs with stdin from `/dev/null`, and shell state (`cd`, `export`) still persists.
- **SSH session stderr is exact.** It is delimited per command with a marker, instead of being collected after a 30 ms pause that let late stderr leak into the next command.
- **Clear error for non-POSIX shells.** Opening a persistent SSH session on a host without a POSIX shell (e.g. Windows OpenSSH) now fails quickly with that explanation.
- **WinRM exit codes:** `exit N` now reports N. Previously it reported success.
- **WinRM timeouts:** the remote command is now stopped or terminated. Previously it kept running.
- **WinRM CMD environment variables** are set through WinRS, so `%NAME%` works.
- **Built-in command groups** use `sudo -n` and fail fast instead of waiting for a password prompt. The `processes` action falls back to plain `ps aux` on systems without GNU `ps`.
- **Strict connection settings.** Unknown connection fields are rejected (a typo such as `pasword` used to be accepted silently). Port and timeouts are coerced to integers, and WinRM `auth` is validated. Profiles saved by 0.2 still load: their unknown keys move under `extra`.
- **Finished jobs are pruned** to the most recent 100.

### Changed
- README: real install commands pinned to tags, a security model section, and testing instructions. Placeholder repository names are removed.
- CI runs ruff and the unit tests on Python 3.10, 3.12 and 3.13. An opt-in live suite (`REMOTE_LIVE=1`) exercises SSH and WinRM through the real MCP stdio server.

## 0.2.0 — 2026-08-22

Initial public version: SSH and WinRM remote administration over MCP, with profiles, sessions, jobs, file transfer and command groups.
