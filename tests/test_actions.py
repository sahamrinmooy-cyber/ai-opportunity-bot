import pytest

from opportunity_bot.actions import ActionDeniedError, ActionPreview, ActionService


def test_url_open_requires_visible_approval(tmp_path):
    opened = []
    previews: list[ActionPreview] = []
    service = ActionService(
        tmp_path,
        lambda preview: previews.append(preview) or False,
        opened.append,
    )

    with pytest.raises(ActionDeniedError, match="not approved"):
        service.execute("open_url", {"url": "https://example.com"})
    assert not opened
    assert previews[0].consequential is True


def test_url_open_runs_only_after_approval_and_needs_no_workspace(tmp_path):
    opened = []
    service = ActionService(None, lambda _preview: True, opened.append)

    result = service.execute("open_url", {"url": "https://example.com"})

    assert opened == ["https://example.com"]
    assert result == "Opened in the isolated browser."


def test_workspace_read_is_confined_to_selected_directory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("local text", encoding="utf-8")
    service = ActionService(workspace, lambda _preview: True, lambda _url: None)

    assert service.execute("read_workspace_file", {"path": "notes.txt"}) == "local text"
    with pytest.raises(ActionDeniedError, match="escapes"):
        service.execute("read_workspace_file", {"path": "../outside.txt"})


def test_workspace_write_requires_approval_and_rejects_extra_fields(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = ActionService(workspace, lambda _preview: False, lambda _url: None)

    with pytest.raises(ActionDeniedError, match="not approved"):
        service.execute("write_workspace_file", {"path": "notes.txt", "content": "draft"})
    assert not (workspace / "notes.txt").exists()
    with pytest.raises(ActionDeniedError, match="schema"):
        service.execute(
            "write_workspace_file", {"path": "notes.txt", "content": "draft", "overwrite": True}
        )


def test_approved_workspace_write_creates_file_after_showing_preview(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = "approved text " * 40
    previews = []
    service = ActionService(
        workspace, lambda preview: previews.append(preview) or True, lambda _url: None
    )

    service.execute("write_workspace_file", {"path": "notes.txt", "content": content})

    assert (workspace / "notes.txt").read_text(encoding="utf-8") == content
    assert "create notes.txt" in previews[0].description
    assert previews[0].details == content


def test_replacing_existing_file_requires_approval(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.txt"
    target.write_text("original", encoding="utf-8")
    previews = []
    service = ActionService(
        workspace,
        lambda preview: previews.append(preview) or False,
        lambda _url: None,
    )

    with pytest.raises(ActionDeniedError, match="not approved"):
        service.execute("write_workspace_file", {"path": "notes.txt", "content": "replacement"})

    assert target.read_text(encoding="utf-8") == "original"
    assert "replace existing" in previews[0].description
    assert previews[0].details == "replacement"


def test_unrestricted_shell_action_is_not_available(tmp_path):
    service = ActionService(tmp_path, lambda _preview: True, lambda _url: None)
    with pytest.raises(ActionDeniedError, match="Unsupported action"):
        service.execute("run_shell", {"command": "echo unsafe"})


@pytest.mark.parametrize("action", ["send_message", "submit_form", "download_file", "merge"])
def test_unimplemented_external_actions_are_rejected(tmp_path, action):
    service = ActionService(tmp_path, lambda _preview: True, lambda _url: None)

    with pytest.raises(ActionDeniedError, match="Unsupported action"):
        service.execute(action, {})


def test_windows_absolute_and_traversal_paths_are_rejected_on_all_platforms(tmp_path):
    service = ActionService(tmp_path, lambda _preview: True, lambda _url: None)

    with pytest.raises(ActionDeniedError, match="Absolute paths"):
        service.execute("read_workspace_file", {"path": r"C:\Users\someone\secret.txt"})
    with pytest.raises(ActionDeniedError, match="escapes"):
        service.execute("read_workspace_file", {"path": r"..\outside.txt"})


def test_symlink_cannot_escape_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (workspace / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Symlinks are unavailable in this environment: {error}")
    service = ActionService(workspace, lambda _preview: True, lambda _url: None)

    with pytest.raises(ActionDeniedError, match="escapes"):
        service.execute("write_workspace_file", {"path": "linked/file.txt", "content": "unsafe"})
