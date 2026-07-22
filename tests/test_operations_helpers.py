from pathlib import Path

from ssh_winrm_mcp.operations import _detect_language, _script_command


def test_detect_language_from_suffix():
    assert _detect_language("auto", "winrm", Path("test.py")) == "python"
    assert _detect_language("auto", "ssh", None) == "bash"


def test_ssh_script_arguments_are_quoted():
    command, shell = _script_command("ssh", "/tmp/a.sh", "bash", None, ["hello world"])
    assert shell == "sh"
    assert "'hello world'" in command


def test_winrm_script_arguments_are_quoted():
    command, shell = _script_command("winrm", r"C:\\Temp\\a.ps1", "powershell", None, ["hello world"])
    assert shell == "powershell"
    assert "'hello world'" in command
