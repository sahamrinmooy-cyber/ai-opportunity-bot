"""Explicitly gated, allowlisted local actions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit


class ActionDeniedError(PermissionError):
    """Raised for unapproved or unsupported local actions."""


@dataclass(frozen=True)
class ActionPreview:
    action: str
    description: str
    consequential: bool
    details: str = ""


ApprovalCallback = Callable[[ActionPreview], bool]
BrowserOpenCallback = Callable[[str], None]


class ActionService:
    def __init__(
        self,
        workspace: Path | None,
        approve: ApprovalCallback,
        browser_open: BrowserOpenCallback,
        max_file_bytes: int = 1_000_000,
    ):
        self.workspace = workspace.resolve() if workspace is not None else None
        self._approve = approve
        self._browser_open = browser_open
        self.max_file_bytes = max_file_bytes

    def execute(self, action: str, arguments: dict[str, object]) -> str:
        if not isinstance(arguments, dict):
            raise ActionDeniedError("Action arguments must be an object.")
        if action == "open_url":
            return self._open_url(arguments)
        if action == "read_workspace_file":
            return self._read_file(arguments)
        if action == "write_workspace_file":
            return self._write_file(arguments)
        raise ActionDeniedError(f"Unsupported action: {action}.")

    def _open_url(self, arguments: dict[str, object]) -> str:
        self._require_fields(arguments, {"url"})
        url = arguments["url"]
        if not isinstance(url, str):
            raise ActionDeniedError("URL must be text.")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise ActionDeniedError("URL contains invalid authority or port data.") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ActionDeniedError("Only valid HTTP or HTTPS URLs can be opened.")
        if port == 0:
            raise ActionDeniedError("URL contains an invalid port.")
        preview = ActionPreview("open_url", f"Open {url} in the isolated browser", True)
        self._require_approval(preview)
        self._browser_open(url)
        return "Opened in the isolated browser."

    def _read_file(self, arguments: dict[str, object]) -> str:
        self._require_fields(arguments, {"path"})
        path = self._workspace_path(arguments["path"])
        if not path.is_file():
            raise FileNotFoundError("The requested workspace file does not exist.")
        if path.stat().st_size > self.max_file_bytes:
            raise ActionDeniedError("Workspace file exceeds the 1 MB read limit.")
        return path.read_text(encoding="utf-8")

    def _write_file(self, arguments: dict[str, object]) -> str:
        self._require_fields(arguments, {"path", "content"})
        path = self._workspace_path(arguments["path"])
        content = arguments["content"]
        if not isinstance(content, str):
            raise ActionDeniedError("File content must be text.")
        if len(content.encode("utf-8")) > self.max_file_bytes:
            raise ActionDeniedError("Workspace file exceeds the 1 MB write limit.")
        existing = "replace existing" if path.exists() else "create"
        preview = ActionPreview(
            "write_workspace_file",
            f"{existing} {path.relative_to(self.workspace)} in {self.workspace}",
            True,
            content,
        )
        self._require_approval(preview)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote {path.name} in the selected workspace."

    def _workspace_path(self, value: object) -> Path:
        if self.workspace is None:
            raise ActionDeniedError("Choose a workspace directory first.")
        if not isinstance(value, str) or not value.strip():
            raise ActionDeniedError("Workspace path must be a non-empty relative path.")
        windows_path = PureWindowsPath(value)
        candidate = Path(value.replace("\\", "/"))
        if candidate.is_absolute() or windows_path.is_absolute() or windows_path.drive:
            raise ActionDeniedError("Absolute paths are not allowed.")
        path = (self.workspace / candidate).resolve()
        try:
            path.relative_to(self.workspace)
        except ValueError:
            raise ActionDeniedError("Path escapes the selected workspace.") from None
        return path

    @staticmethod
    def _require_fields(arguments: dict[str, object], expected: set[str]) -> None:
        if set(arguments) != expected:
            raise ActionDeniedError("Action arguments do not match the supported schema.")

    def _require_approval(self, preview: ActionPreview) -> None:
        if not self._approve(preview):
            raise ActionDeniedError("Action was not approved.")
