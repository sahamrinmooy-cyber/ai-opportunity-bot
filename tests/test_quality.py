import io
import subprocess
import sys

import pytest

from opportunity_bot.quality import (
    ApprovedChecks,
    CheckPreview,
    CheckResult,
    QualityCheckDeniedError,
    _run_check,
)


def test_checks_are_fixed_and_require_approval(tmp_path):
    seen = []
    previews = []
    runner = ApprovedChecks(
        tmp_path,
        lambda preview: previews.append(preview) or True,
        lambda preview, timeout: (
            seen.append((preview, timeout)) or CheckResult(preview.name, 0, "passed", "", False)
        ),
    )

    result = runner.run("Tests (pytest)")

    assert result.returncode == 0
    preview, timeout = seen[0]
    assert preview.command == (sys.executable, "-m", "pytest", "-q")
    assert preview.workspace == tmp_path.resolve()
    assert "not sandboxed" in preview.warning
    assert timeout <= 120
    assert previews == [preview]


def test_unknown_or_unapproved_checks_never_run(tmp_path):
    called = []
    checks = ApprovedChecks(
        tmp_path,
        lambda _preview: False,
        lambda *_args: called.append(True),
    )

    with pytest.raises(QualityCheckDeniedError, match="Only the listed"):
        checks.run("run arbitrary shell")
    with pytest.raises(QualityCheckDeniedError, match="not approved"):
        checks.run("Lint (Ruff)")

    assert not called


def test_command_timeout_is_bounded(tmp_path, monkeypatch):
    calls = []

    class FakeProcess:
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        pid = 1234

        def __init__(self, *_args, **_kwargs):
            self.waits = 0
            self.killed = False
            self.signals = []

        def wait(self, timeout=None):
            calls.append(timeout)
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("command", timeout)
            return 0

        def send_signal(self, value):
            self.signals.append(value)

        def kill(self):
            self.killed = True

    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    preview = CheckPreview(
        "Tests (pytest)",
        (sys.executable, "-m", "pytest"),
        tmp_path,
        "warning",
        timeout_seconds=7,
    )

    result = _run_check(preview, 7)

    assert result.timed_out is True
    assert result.returncode == 124
    assert "7 seconds" in result.stderr
    assert calls == [7, 2, None]


def test_check_command_filters_environment_secrets_and_truncates_output(tmp_path, monkeypatch):
    seen = {}

    class FakeProcess:
        stdout = io.BytesIO(b"x" * 33_000)
        stderr = io.BytesIO(b"clean")
        returncode = 0

        def wait(self, timeout=None):
            seen["timeout"] = timeout
            return self.returncode

    def run(_command, **kwargs):
        seen.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", run)
    monkeypatch.setenv("AI_API_TOKEN", "not-for-the-test")
    preview = CheckPreview(
        "Lint (Ruff)",
        ("python", "-m", "ruff"),
        tmp_path,
        "warning",
        timeout_seconds=10,
    )

    result = _run_check(preview, 10)

    assert "AI_API_TOKEN" not in seen["env"]
    assert seen["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert seen["shell"] is False
    assert seen["stdout"] is subprocess.PIPE
    assert "output truncated" in result.stdout
    assert result.stderr == "clean"


def test_check_runner_executes_a_command_without_a_shell(tmp_path):
    preview = CheckPreview(
        "Runner smoke test",
        (sys.executable, "-c", "print('bounded runner works')"),
        tmp_path,
        "test only",
        timeout_seconds=5,
    )

    result = _run_check(preview, 5)

    assert result.returncode == 0
    assert result.stdout.strip() == "bounded runner works"
    assert not result.timed_out
