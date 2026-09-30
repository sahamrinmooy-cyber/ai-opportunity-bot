"""Read-only loader for user-selected Markdown agent-profile libraries."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

MAX_AGENT_PROFILES = 1_000
MAX_AGENT_PROFILE_BYTES = 64_000
MAX_AGENT_INSTRUCTIONS_CHARACTERS = 39_000
_IGNORED_DIRECTORIES = {
    ".git",
    ".github",
    "integrations",
    "scripts",
    "examples",
}
_FRONTMATTER_NAME = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
_FRONTMATTER_DESCRIPTION = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)


class AgentLibraryError(ValueError):
    """Raised when an imported agent profile is malformed or exceeds limits."""


@dataclass(frozen=True)
class AgentProfile:
    name: str
    description: str
    source_path: str
    instructions: str


class AgentLibrary:
    """Lists and reads prompt files without executing source-repository content."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        if not self.root.is_dir():
            raise AgentLibraryError("Select an existing agent-library directory.")

    def list_profiles(self, limit: int = MAX_AGENT_PROFILES) -> tuple[str, ...]:
        if limit < 1 or limit > MAX_AGENT_PROFILES:
            raise ValueError(f"Profile limit must be between 1 and {MAX_AGENT_PROFILES}.")
        paths = []
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
                if not filename.lower().endswith(".md"):
                    continue
                path = current_path / filename
                if path.is_symlink() or not self._has_agent_frontmatter(path):
                    continue
                paths.append(path.relative_to(self.root).as_posix())
                if len(paths) > limit:
                    raise AgentLibraryError(f"Agent library exceeds the {limit}-profile limit.")
        return tuple(paths)

    @staticmethod
    def _has_agent_frontmatter(path: Path) -> bool:
        try:
            with path.open("rb") as profile_file:
                header = profile_file.read(2_048).decode("utf-8")
        except (OSError, UnicodeError):
            return False
        if not header.startswith("---"):
            return False
        _, separator, remainder = header.partition("---")
        frontmatter, end_marker, _ = remainder.partition("---")
        return bool(separator and end_marker and _FRONTMATTER_NAME.search(frontmatter))

    def load(self, source_path: str) -> AgentProfile:
        path = self._resolve(source_path)
        if not path.is_file():
            raise AgentLibraryError("Selected agent profile is not a regular file.")
        if path.stat().st_size > MAX_AGENT_PROFILE_BYTES:
            raise AgentLibraryError("Agent profile exceeds the 64 KB size limit.")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise AgentLibraryError("Could not read selected profile as UTF-8 text.") from None

        if not text.startswith("---"):
            raise AgentLibraryError("Agent profile must start with YAML frontmatter.")
        _, _, remainder = text.partition("---")
        frontmatter, end_marker, body = remainder.partition("---")
        if not end_marker:
            raise AgentLibraryError("Agent profile frontmatter is not closed.")
        name_match = _FRONTMATTER_NAME.search(frontmatter)
        description_match = _FRONTMATTER_DESCRIPTION.search(frontmatter)
        name = self._unquote(name_match.group(1)) if name_match else path.stem
        description = self._unquote(description_match.group(1)) if description_match else ""
        instructions = body.strip()
        if not name or not instructions:
            raise AgentLibraryError("Agent profile must have a name and instructions.")
        if len(instructions) > MAX_AGENT_INSTRUCTIONS_CHARACTERS:
            raise AgentLibraryError("Agent instructions exceed the 39,000-character limit.")
        return AgentProfile(
            name=name,
            description=description,
            source_path=path.relative_to(self.root).as_posix(),
            instructions=instructions,
        )

    def _resolve(self, value: str) -> Path:
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise AgentLibraryError("Agent profile path is invalid.")
        candidate = Path(value.replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            raise AgentLibraryError("Agent profile path must stay inside the selected library.")
        path = self.root / candidate
        cursor = self.root
        for part in candidate.parts:
            if part in {"", "."}:
                continue
            cursor = cursor / part
            if cursor.is_symlink():
                raise AgentLibraryError("Symlink profile paths are not allowed.")
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise AgentLibraryError("Agent profile path escapes the selected library.") from None
        return resolved

    @staticmethod
    def _unquote(value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]
        return value
