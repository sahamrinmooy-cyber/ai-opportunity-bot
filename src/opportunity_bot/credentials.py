"""OS credential-store adapter; credentials are never written to project files."""

from __future__ import annotations

from typing import Protocol


class CredentialStore(Protocol):
    def set(
        self,
        project_id: str,
        secret: str,
        provider: str = "openai-compatible",
    ) -> None: ...

    def get(self, project_id: str, provider: str = "openai-compatible") -> str | None: ...


class CredentialStoreError(RuntimeError):
    """Raised when the operating system credential store is unavailable."""


class KeyringCredentialStore:
    def __init__(self, service_name: str = "ai-opportunity-bot"):
        self.service_name = service_name

    def set(
        self,
        project_id: str,
        secret: str,
        provider: str = "openai-compatible",
    ) -> None:
        if not secret.strip():
            raise ValueError("Credential cannot be empty.")
        import keyring

        account = self._account(project_id, provider)
        try:
            keyring.set_password(self.service_name, account, secret)
        except keyring.errors.KeyringError:
            raise CredentialStoreError("Could not write to the OS credential store.") from None

    def get(self, project_id: str, provider: str = "openai-compatible") -> str | None:
        import keyring

        account = self._account(project_id, provider)
        try:
            return keyring.get_password(self.service_name, account)
        except keyring.errors.KeyringError:
            raise CredentialStoreError("Could not read from the OS credential store.") from None

    @staticmethod
    def _account(project_id: str, provider: str) -> str:
        if provider not in {"openai-compatible", "anthropic"}:
            raise ValueError("Credential provider is not supported.")
        return project_id if provider == "openai-compatible" else f"{project_id}:{provider}"
