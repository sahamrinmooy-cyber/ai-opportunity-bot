"""OS credential-store adapter; credentials are never written to project files."""

from __future__ import annotations

from typing import Protocol


class CredentialStore(Protocol):
    def set(self, project_id: str, secret: str) -> None: ...

    def get(self, project_id: str) -> str | None: ...


class CredentialStoreError(RuntimeError):
    """Raised when the operating system credential store is unavailable."""


class KeyringCredentialStore:
    def __init__(self, service_name: str = "ai-opportunity-bot"):
        self.service_name = service_name

    def set(self, project_id: str, secret: str) -> None:
        if not secret.strip():
            raise ValueError("Credential cannot be empty.")
        import keyring

        try:
            keyring.set_password(self.service_name, project_id, secret)
        except keyring.errors.KeyringError:
            raise CredentialStoreError("Could not write to the OS credential store.") from None

    def get(self, project_id: str) -> str | None:
        import keyring

        try:
            return keyring.get_password(self.service_name, project_id)
        except keyring.errors.KeyringError:
            raise CredentialStoreError("Could not read from the OS credential store.") from None
