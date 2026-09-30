"""Bounded workspace context and review-first whole-file change proposals."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Protocol

MAX_CONTEXT_FILES = 5
MAX_CONTEXT_FILE_BYTES = 32_000
MAX_PROPOSAL_FILES = 10
MAX_PROPOSAL_FILE_BYTES = 256_000
MAX_PROPOSAL_TOTAL_BYTES = 1_000_000
MAX_FILE_LIST_ENTRIES = 500

_IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}
_SENSITIVE_NAMES = {
    ".aws",
    ".ssh",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
}
_SECRET_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f]")


class WorkspaceError(ValueError):
    """Raised when a workspace path or coding proposal is unsafe or invalid."""


class WorkspaceConflictError(WorkspaceError):
    """Raised when a file changed after its proposal context was read."""


class ProposalApproval(Protocol):
    def __call__(self, proposal: CodeProposal) -> bool: ...


@dataclass(frozen=True)
class WorkspaceListing:
    paths: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    content: str
    sha256: str


@dataclass(frozen=True)
class ProposedFile:
    path: str
    content: str
    expected_sha256: str | None
    original_content: str


@dataclass(frozen=True)
class CodeProposal:
    summary: str
    files: tuple[ProposedFile, ...]

    def unified_diff(self) -> str:
        pieces = []
        for file in self.files:
            pieces.extend(
                difflib.unified_diff(
                    file.original_content.splitlines(keepends=True),
                    file.content.splitlines(keepends=True),
                    fromfile=f"a/{file.path}" if file.expected_sha256 is not None else "/dev/null",
                    tofile=f"b/{file.path}",
                )
            )
        return "".join(pieces)


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        if not self.root.is_dir():
            raise WorkspaceError("Choose an existing workspace directory.")

    def list_files(self, limit: int = MAX_FILE_LIST_ENTRIES) -> WorkspaceListing:
        if limit < 1:
            raise ValueError("File listing limit must be positive.")
        results = []
        truncated = False
        for current, directories, filenames in os.walk(self.root, followlinks=False):
            current_path = Path(current)
            directories[:] = sorted(
                directory
                for directory in directories
                if directory not in _IGNORED_DIRECTORIES
                and not directory.startswith(".")
                and not (current_path / directory).is_symlink()
            )
            for filename in sorted(filenames):
                path = current_path / filename
                if path.is_symlink() or self._is_sensitive(path.relative_to(self.root).as_posix()):
                    continue
                results.append(path.relative_to(self.root).as_posix())
                if len(results) > limit:
                    truncated = True
                    return WorkspaceListing(tuple(results[:limit]), truncated)
        return WorkspaceListing(tuple(results), truncated)

    def read_context(self, paths: list[str]) -> tuple[FileSnapshot, ...]:
        if not paths:
            raise WorkspaceError("Choose at least one workspace file as model context.")
        if len(paths) > MAX_CONTEXT_FILES:
            raise WorkspaceError(f"Select no more than {MAX_CONTEXT_FILES} context files.")
        if len(set(paths)) != len(paths):
            raise WorkspaceError("Context file paths must be unique.")

        snapshots = []
        total = 0
        for value in paths:
            path = self._resolve_path(value)
            if not path.is_file():
                raise WorkspaceError(f"Workspace context is not a regular file: {value}")
            data = path.read_bytes()
            if len(data) > MAX_CONTEXT_FILE_BYTES:
                raise WorkspaceError(f"Context file exceeds 32 KB: {value}")
            total += len(data)
            if total > MAX_CONTEXT_FILES * MAX_CONTEXT_FILE_BYTES:
                raise WorkspaceError("Selected context exceeds the 160 KB total limit.")
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                raise WorkspaceError(f"Context file is not UTF-8 text: {value}") from None
            snapshots.append(
                FileSnapshot(
                    path=self._relative_path(path),
                    content=content,
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            )
        return tuple(snapshots)

    def parse_proposal(self, response: str, context: tuple[FileSnapshot, ...]) -> CodeProposal:
        if len(response.encode("utf-8")) > MAX_PROPOSAL_FILES * MAX_PROPOSAL_FILE_BYTES:
            raise WorkspaceError("AI proposal exceeds the 2.5 MB response limit.")
        try:
            data = json.loads(response)
        except (json.JSONDecodeError, TypeError):
            raise WorkspaceError(
                "AI response must be raw JSON; markdown fences are not accepted."
            ) from None
        if (
            not isinstance(data, dict)
            or set(data) != {"summary", "files"}
            or not isinstance(data["summary"], str)
            or not data["summary"].strip()
            or len(data["summary"]) > 1_000
            or not isinstance(data["files"], list)
            or not 1 <= len(data["files"]) <= MAX_PROPOSAL_FILES
        ):
            raise WorkspaceError("AI response does not match the supported change proposal schema.")

        context_by_path = {snapshot.path: snapshot for snapshot in context}
        seen = set()
        proposed = []
        total_content_bytes = 0
        for entry in data["files"]:
            if not isinstance(entry, dict) or set(entry) != {"path", "content"}:
                raise WorkspaceError("Each proposed file must contain only path and content.")
            value, content = entry["path"], entry["content"]
            if not isinstance(value, str) or not isinstance(content, str):
                raise WorkspaceError("Proposed file paths and contents must be text.")
            path = self._resolve_path(value)
            relative = self._relative_path(path)
            if relative in seen:
                raise WorkspaceError(f"Proposal repeats a file path: {relative}")
            seen.add(relative)
            content_bytes = len(content.encode("utf-8"))
            if content_bytes > MAX_PROPOSAL_FILE_BYTES:
                raise WorkspaceError(f"Proposed file exceeds 256 KB: {relative}")
            total_content_bytes += content_bytes
            if total_content_bytes > MAX_PROPOSAL_TOTAL_BYTES:
                raise WorkspaceError("Proposal contents exceed the 1 MB total limit.")
            snapshot = context_by_path.get(relative)
            if snapshot is None:
                if path.exists():
                    raise WorkspaceError(
                        "Existing file must be included in context before it can be changed: "
                        f"{relative}"
                    )
                original, expected_hash = "", None
            else:
                original, expected_hash = snapshot.content, snapshot.sha256
            proposed.append(ProposedFile(relative, content, expected_hash, original))
        return CodeProposal(data["summary"].strip(), tuple(proposed))

    def apply_proposal(
        self,
        proposal: CodeProposal,
        approve: ProposalApproval,
    ) -> None:
        self._validate_proposal(proposal)
        if not approve(proposal):
            raise WorkspaceError("Change proposal was not approved.")

        destinations = []
        for item in proposal.files:
            path = self._resolve_path(item.path)
            if item.expected_sha256 is None:
                if path.exists():
                    raise WorkspaceConflictError(f"File appeared after proposal: {item.path}")
            else:
                if not path.is_file():
                    raise WorkspaceConflictError(f"File disappeared after proposal: {item.path}")
                actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual_hash != item.expected_sha256:
                    raise WorkspaceConflictError(f"File changed after proposal: {item.path}")
            destinations.append((item, path))

        for item, path in destinations:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary_name = ""
            try:
                with tempfile.NamedTemporaryFile(
                    "w",
                    encoding="utf-8",
                    dir=path.parent,
                    delete=False,
                    newline="",
                ) as temporary:
                    temporary.write(item.content)
                    temporary_name = temporary.name
                os.replace(temporary_name, path)
            finally:
                if temporary_name and os.path.exists(temporary_name):
                    os.unlink(temporary_name)

    def _validate_proposal(self, proposal: CodeProposal) -> None:
        if (
            not isinstance(proposal, CodeProposal)
            or not isinstance(proposal.summary, str)
            or not proposal.summary.strip()
            or len(proposal.summary) > 1_000
            or not isinstance(proposal.files, tuple)
            or not 1 <= len(proposal.files) <= MAX_PROPOSAL_FILES
        ):
            raise WorkspaceError("Only validated coding proposals can be applied.")
        seen = set()
        total_bytes = 0
        for item in proposal.files:
            if (
                not isinstance(item, ProposedFile)
                or not isinstance(item.content, str)
                or not isinstance(item.original_content, str)
            ):
                raise WorkspaceError("Proposal contains an invalid file record.")
            path = self._resolve_path(item.path)
            relative = self._relative_path(path)
            if relative != item.path or relative in seen:
                raise WorkspaceError("Proposal contains a duplicate or non-canonical path.")
            seen.add(relative)
            content_bytes = len(item.content.encode("utf-8"))
            total_bytes += content_bytes
            if content_bytes > MAX_PROPOSAL_FILE_BYTES or total_bytes > MAX_PROPOSAL_TOTAL_BYTES:
                raise WorkspaceError("Proposal contents exceed the supported size limit.")
            if item.expected_sha256 is None:
                if item.original_content:
                    raise WorkspaceError("New-file proposal contains unexpected original content.")
            elif (
                not re.fullmatch(r"[a-f0-9]{64}", item.expected_sha256)
                or hashlib.sha256(item.original_content.encode("utf-8")).hexdigest()
                != item.expected_sha256
            ):
                raise WorkspaceError("Proposal's original file snapshot is invalid.")

    def _resolve_path(self, value: object) -> Path:
        if (
            not isinstance(value, str)
            or not value.strip()
            or _CONTROL_CHARACTERS.search(value)
            or self._is_sensitive(value.replace("\\", "/"))
        ):
            raise WorkspaceError("Workspace path is empty or names a protected file.")
        windows_path = PureWindowsPath(value)
        candidate = Path(value.replace("\\", "/"))
        if candidate.is_absolute() or windows_path.is_absolute() or windows_path.drive:
            raise WorkspaceError("Absolute paths are not allowed.")
        if ".." in re.split(r"[\\/]+", value):
            raise WorkspaceError("Parent-directory traversal is not allowed.")
        lexical = self.root / candidate
        cursor = self.root
        for part in candidate.parts:
            if part in {"", "."}:
                continue
            cursor = cursor / part
            if cursor.is_symlink():
                raise WorkspaceError("Symlink paths are not allowed.")
        resolved = lexical.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise WorkspaceError("Path escapes the selected workspace.") from None
        if self._is_sensitive(resolved.relative_to(self.root).as_posix()):
            raise WorkspaceError("Workspace path names a protected file.")
        return resolved

    def _relative_path(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    @staticmethod
    def _is_sensitive(value: str) -> bool:
        parts = re.split(r"[\\/]+", value.lower())
        return any(
            part in _SENSITIVE_NAMES
            or part == ".env"
            or part.startswith(".env.")
            or Path(part).suffix in _SECRET_SUFFIXES
            for part in parts
        )
