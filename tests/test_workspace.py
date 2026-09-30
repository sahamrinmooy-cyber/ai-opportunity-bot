import json

import pytest

from opportunity_bot.workspace import (
    CodeProposal,
    ProposedFile,
    Workspace,
    WorkspaceConflictError,
    WorkspaceError,
)


def _proposal(path, content, summary="Test change"):
    return json.dumps({"summary": summary, "files": [{"path": path, "content": content}]})


def test_file_listing_is_bounded_and_hides_secrets_and_generated_folders(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("print('hello')", encoding="utf-8")
    (root / ".env").write_text("SECRET=do-not-show", encoding="utf-8")
    (root / "private.pem").write_text("private-key", encoding="utf-8")
    generated = root / ".venv"
    generated.mkdir()
    (generated / "site.py").write_text("generated", encoding="utf-8")
    (root / "README.md").write_text("docs", encoding="utf-8")

    listing = Workspace(root).list_files(limit=1)

    assert listing.paths == ("README.md",)
    assert listing.truncated is True
    assert ".env" not in listing.paths
    assert "private.pem" not in listing.paths


def test_context_reads_only_selected_bounded_utf8_files(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "module.py").write_text("print('safe')", encoding="utf-8")
    (root / ".env").write_text("SECRET=do-not-read", encoding="utf-8")
    (root / "large.py").write_text("x" * 32_001, encoding="utf-8")
    workspace = Workspace(root)

    snapshot = workspace.read_context(["module.py"])[0]

    assert snapshot.content == "print('safe')"
    assert len(snapshot.sha256) == 64
    for protected in (".env", "large.py", "../outside.py", r"C:\private.py"):
        with pytest.raises(WorkspaceError):
            workspace.read_context([protected])


def test_parse_proposal_produces_a_reviewable_diff_for_context_file(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("print('old')\n", encoding="utf-8")
    workspace = Workspace(root)
    context = workspace.read_context(["app.py"])

    proposal = workspace.parse_proposal(
        _proposal("app.py", "print('new')\n", summary="Improve greeting"),
        context,
    )

    assert proposal.summary == "Improve greeting"
    assert "-print('old')" in proposal.unified_diff()
    assert "+print('new')" in proposal.unified_diff()


@pytest.mark.parametrize(
    "response",
    [
        "```json\n{}\n```",
        '{"summary":"no files","files":[]}',
        '{"summary":"bad path","files":[{"path":"../outside.txt","content":"x"}]}',
        '{"summary":"secret","files":[{"path":".env","content":"KEY=x"}]}',
        '{"summary":"not context","files":[{"path":"other.py","content":"replace"}]}',
        '{"summary":"duplicate","files":[{"path":"new.py","content":"a"},{"path":"new.py","content":"b"}]}',
    ],
)
def test_parse_proposal_rejects_invalid_untrusted_model_output(tmp_path, response):
    root = tmp_path / "project"
    root.mkdir()
    (root / "other.py").write_text("not selected", encoding="utf-8")

    with pytest.raises(WorkspaceError):
        Workspace(root).parse_proposal(response, ())


def test_apply_proposal_requires_approval_and_preserves_file_on_denial(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.py"
    target.write_text("print('old')\n", encoding="utf-8")
    workspace = Workspace(root)
    context = workspace.read_context(["app.py"])
    proposal = workspace.parse_proposal(_proposal("app.py", "print('new')\n"), context)
    previews: list[CodeProposal] = []

    with pytest.raises(WorkspaceError, match="not approved"):
        workspace.apply_proposal(proposal, lambda item: previews.append(item) or False)

    assert target.read_text(encoding="utf-8") == "print('old')\n"
    assert previews == [proposal]


def test_apply_proposal_detects_stale_files_before_writing(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.py"
    target.write_text("print('old')\n", encoding="utf-8")
    workspace = Workspace(root)
    context = workspace.read_context(["app.py"])
    proposal = workspace.parse_proposal(_proposal("app.py", "print('new')\n"), context)
    target.write_text("user edit\n", encoding="utf-8")

    with pytest.raises(WorkspaceConflictError, match="changed after proposal"):
        workspace.apply_proposal(proposal, lambda _item: True)

    assert target.read_text(encoding="utf-8") == "user edit\n"


def test_apply_proposal_detects_a_new_file_conflict(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    workspace = Workspace(root)
    proposal = workspace.parse_proposal(_proposal("new.py", "proposed\n"), ())
    target = root / "new.py"
    target.write_text("created locally\n", encoding="utf-8")

    with pytest.raises(WorkspaceConflictError, match="File appeared"):
        workspace.apply_proposal(proposal, lambda _item: True)

    assert target.read_text(encoding="utf-8") == "created locally\n"


def test_apply_proposal_validates_even_directly_constructed_records(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    workspace = Workspace(root)
    forged = CodeProposal(
        "forged path",
        (ProposedFile("../outside.txt", "not allowed", None, ""),),
    )

    with pytest.raises(WorkspaceError):
        workspace.apply_proposal(forged, lambda _item: True)
    assert not (tmp_path / "outside.txt").exists()


def test_approved_proposal_can_create_and_update_files(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("print('old')\n", encoding="utf-8")
    workspace = Workspace(root)
    context = workspace.read_context(["app.py"])
    proposal = workspace.parse_proposal(
        json.dumps(
            {
                "summary": "Add a helper",
                "files": [
                    {"path": "app.py", "content": "print('updated')\n"},
                    {"path": "src/helper.py", "content": "def helper():\n    return 1\n"},
                ],
            }
        ),
        context,
    )

    workspace.apply_proposal(proposal, lambda item: len(item.files) == 2)

    assert (root / "app.py").read_text(encoding="utf-8") == "print('updated')\n"
    assert (root / "src" / "helper.py").read_text(
        encoding="utf-8"
    ) == "def helper():\n    return 1\n"


def test_workspace_rejects_symlink_context_and_proposal_paths(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    try:
        (root / "linked.txt").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Symlinks are unavailable in this environment: {error}")

    workspace = Workspace(root)

    with pytest.raises(WorkspaceError, match="Symlink"):
        workspace.read_context(["linked.txt"])
    with pytest.raises(WorkspaceError, match="Symlink"):
        workspace.parse_proposal(_proposal("linked.txt", "overwrite"), ())
