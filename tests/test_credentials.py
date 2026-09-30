import sys
from types import SimpleNamespace

import pytest

from opportunity_bot.credentials import (
    CredentialStoreError,
    KeyringCredentialStore,
)
from opportunity_bot.projects import ChatProject, ProjectStore


def test_keyring_adapter_uses_os_store_without_a_file(tmp_path, monkeypatch):
    calls = []
    fake_keyring = SimpleNamespace(
        set_password=lambda *args: calls.append(("set", args)),
        get_password=lambda *args: calls.append(("get", args)) or "secret-from-vault",
        errors=SimpleNamespace(KeyringError=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    vault = KeyringCredentialStore()
    store = ProjectStore(tmp_path / "projects")
    store.save(ChatProject.create("Test chatbot"))

    vault.set("project-id", "test-secret")
    assert vault.get("project-id") == "secret-from-vault"

    assert calls == [
        ("set", ("ai-opportunity-bot", "project-id", "test-secret")),
        ("get", ("ai-opportunity-bot", "project-id")),
    ]
    project_file = next((tmp_path / "projects").glob("*.json"))
    assert "test-secret" not in project_file.read_text(encoding="utf-8")


def test_keyring_failure_is_reported_without_echoing_secret(monkeypatch):
    def fail_set(*_args):
        raise RuntimeError("backend failure")

    fake_keyring = SimpleNamespace(
        set_password=fail_set,
        get_password=lambda *_args: None,
        errors=SimpleNamespace(KeyringError=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

    with pytest.raises(CredentialStoreError, match="OS credential store") as error:
        KeyringCredentialStore().set("project-id", "test-secret")
    assert "test-secret" not in str(error.value)


def test_credentials_are_separated_by_provider(monkeypatch):
    values = {}
    fake_keyring = SimpleNamespace(
        set_password=lambda service, account, secret: values.__setitem__(
            (service, account), secret
        ),
        get_password=lambda service, account: values.get((service, account)),
        errors=SimpleNamespace(KeyringError=RuntimeError),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    vault = KeyringCredentialStore()

    vault.set("project-id", "openai-key")
    vault.set("project-id", "anthropic-key", "anthropic")

    assert vault.get("project-id") == "openai-key"
    assert vault.get("project-id", "anthropic") == "anthropic-key"
