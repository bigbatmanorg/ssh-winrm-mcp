import json
import stat

import pytest

from ssh_winrm_mcp.profiles import ProfileStore


def test_inventory_initializes_and_reports_status(tmp_path):
    path = tmp_path / "inventory.json"
    store = ProfileStore(path)
    assert store.status()["exists"] is False
    result = store.initialize()
    assert result["created"] is True
    assert path.exists()
    assert json.loads(path.read_text())["version"] == 1


def test_profile_full_crud_redaction_and_tags(tmp_path):
    path = tmp_path / "inventory.json"
    store = ProfileStore(path)
    store.create(
        "lab",
        {
            "protocol": "ssh",
            "host": "lab.local",
            "username": "root",
            "password": "secret",
            "tags": ["linux", "lab"],
        },
    )

    assert store.names() == ["lab"]
    assert store.list_safe()["lab"]["password"] == "***REDACTED***"
    assert list(store.list_safe(tag="lab")) == ["lab"]
    assert store.list_safe(protocol="winrm") == {}
    assert store.get("lab").resolved_password() == "secret"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    updated = store.update("lab", {"host": "new.local"}, remove_fields=["password"])
    assert updated.host == "new.local"
    assert updated.password is None

    assert store.rename("lab", "linux01") is True
    assert store.names() == ["linux01"]
    assert store.delete("linux01") is True
    assert store.delete("linux01") is False


def test_profile_create_rejects_duplicate(tmp_path):
    store = ProfileStore(tmp_path / "inventory.json")
    settings = {"protocol": "ssh", "host": "one", "username": "root"}
    store.create("same", settings)
    with pytest.raises(ValueError):
        store.create("same", settings)


def test_profile_validation_reports_bad_entries(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "good": {"protocol": "ssh", "host": "one", "username": "root"},
                    "bad": {"protocol": "ssh", "host": ""},
                },
            }
        )
    )
    result = ProfileStore(path).validate()
    assert result["ok"] is False
    assert result["valid_profiles"] == ["good"]
    assert "bad" in result["invalid_profiles"]


def test_inventory_status_survives_corrupt_json(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text("{broken")
    store = ProfileStore(path)
    assert store.status()["load_error"]
    result = store.validate()
    assert result["ok"] is False
    assert result["inventory_error"]
