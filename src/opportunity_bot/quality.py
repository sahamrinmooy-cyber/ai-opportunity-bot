"""Fixed, user-approved quality checks for a selected coding workspace."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MAX_CHECK_SECONDS = 120
MAX_CHECK_OUTPUT_BYTES = 32_000
_SAFE_ENVIRONMENT_MARKERS = (
    "KEY",
    "AUTH",
    "CREDENTIAL",
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "COOKIE",
    "CERT",
)


class QualityCheckDeniedError(PermissionError):
    """Raised when a fixed check is unknown or not explicitly approved."""


@dataclass(frozen=True)
class CheckPreview:
    name: str
    command: tuple[str, ...]
    workspace: Path
    warning: str
    timeout_seconds: int


@dataclass(frozen=True)
class CheckResult:
    name: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


class CheckRunner(Protocol):
    def __call__(self, preview: CheckPreview, timeout_seconds: int) -> CheckResult: ...


class CheckApproval(Protocol):
    def __call__(self, preview: CheckPreview) -> bool: ...


def _read_limited(stream, output: bytearray) -> None:
    while chunk := stream.read(4096):
        remaining = MAX_CHECK_OUTPUT_BYTES + 1 - len(output)
        if remaining > 0:
            output.extend(chunk[:remaining])


def _run_check(preview: CheckPreview, timeout_seconds: int) -> CheckResult:
    safe_env = {
        name: value
        for name, value in os.environ.items()
        if not any(marker in name.upper() for marker in _SAFE_ENVIRONMENT_MARKERS)
    }
    safe_env["PYTHONDONTWRITEBYTECODE"] = "1"
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        list(preview.command),
        cwd=preview.workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=safe_env,
        shell=False,
        creationflags=creation_flags,
        start_new_session=os.name != "nt",
    )
    stdout = bytearray()
    stderr = bytearray()
    stdout_thread = threading.Thread(
        target=_read_limited,
        args=(process.stdout, stdout),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_read_limited,
        args=(process.stderr, stderr),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "nt":
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except OSError:
                process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        returncode = process.wait()

    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    if process.stdout and not stdout_thread.is_alive():
        process.stdout.close()
    if process.stderr and not stderr_thread.is_alive():
        process.stderr.close()
    truncation = "\n[output truncated at 32 KB]"
    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")
    stdout_truncated = len(stdout) > MAX_CHECK_OUTPUT_BYTES
    stderr_truncated = len(stderr) > MAX_CHECK_OUTPUT_BYTES
    stdout_text = stdout_text[:MAX_CHECK_OUTPUT_BYTES]
    stderr_text = stderr_text[:MAX_CHECK_OUTPUT_BYTES]
    if timed_out:
        stderr_text = (stderr_text + f"\nCheck timed out after {timeout_seconds} seconds.").strip()
        returncode = 124
    return CheckResult(
        preview.name,
        returncode,
        stdout_text + (truncation if stdout_truncated else ""),
        stderr_text + (truncation if stderr_truncated else ""),
        timed_out=timed_out,
    )


class ApprovedChecks:
    _allowed_modules = {
        "Tests (pytest)": ("pytest", "-q"),
        "Lint (Ruff)": ("ruff", "check", "."),
    }

    def __init__(
        self,
        workspace: Path,
        approve: CheckApproval,
        runner: CheckRunner | None = None,
        timeout_seconds: int = MAX_CHECK_SECONDS,
    ):
        if not workspace.is_dir():
            raise ValueError("Choose an existing workspace directory.")
        if timeout_seconds < 1 or timeout_seconds > MAX_CHECK_SECONDS:
            raise ValueError(f"Timeout must be between 1 and {MAX_CHECK_SECONDS} seconds.")
        self.workspace = workspace.resolve()
        self._approve = approve
        self._runner = runner or _run_check
        self.timeout_seconds = timeout_seconds

    def run(self, name: str) -> CheckResult:
        preview = self.preview(name)
        if not self._approve(preview):
            raise QualityCheckDeniedError("Quality check was not approved.")
        return self.run_approved(preview)

    def preview(self, name: str) -> CheckPreview:
        if name not in self._allowed_modules:
            raise QualityCheckDeniedError("Only the listed pytest and Ruff checks are available.")
        return CheckPreview(
            name=name,
            command=(sys.executable, "-m", *self._allowed_modules[name]),
            workspace=self.workspace,
            warning=(
                "This runs code/configuration from the selected project. It is not sandboxed. "
                "A project may read or modify files available to your account."
            ),
            timeout_seconds=self.timeout_seconds,
        )

    def run_approved(self, preview: CheckPreview) -> CheckResult:
        if not isinstance(preview, CheckPreview) or preview != self.preview(preview.name):
            raise QualityCheckDeniedError(
                "Check preview is invalid or belongs to another workspace."
            )
        return self._runner(preview, self.timeout_seconds)
