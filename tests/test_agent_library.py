import pytest

from opportunity_bot.agent_library import AgentLibrary, AgentLibraryError


def test_library_lists_profiles_read_only_and_skips_integrations_and_scripts(tmp_path):
    root = tmp_path / "agency-agents"
    (root / "engineering").mkdir(parents=True)
    (root / "integrations" / "claude-code").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / ".git").mkdir()
    (root / "README.md").write_text("# Library overview", encoding="utf-8")
    (root / "CONTRIBUTING.md").write_text("# Contribution guide", encoding="utf-8")
    (root / "engineering" / "reviewer.md").write_text(
        "---\nname: Code Reviewer\ndescription: Constructive reviews\n---\n"
        "# Reviewer\nInstructions only; no code is executed.",
        encoding="utf-8",
    )
    (root / "integrations" / "claude-code" / "converted.md").write_text(
        "---\nname: Converted\n---\nignored",
        encoding="utf-8",
    )
    (root / "scripts" / "automation.md").write_text("---\nname: Script\n---\nignored")
    (root / ".git" / "metadata.md").write_text("---\nname: Hidden\n---\nignored")

    library = AgentLibrary(root)

    assert library.list_profiles() == ("engineering/reviewer.md",)
    profile = library.load("engineering/reviewer.md")
    assert profile.name == "Code Reviewer"
    assert profile.description == "Constructive reviews"
    assert profile.source_path == "engineering/reviewer.md"
    assert "Instructions only; no code is executed." in profile.instructions


@pytest.mark.parametrize(
    "path",
    ["../outside.md", r"C:\outside.md", ".git/config.md", "secrets/.env.md"],
)
def test_library_rejects_traversal_absolute_and_secret_paths(tmp_path, path):
    root = tmp_path / "library"
    root.mkdir()
    (tmp_path / "outside.md").write_text("outside", encoding="utf-8")
    library = AgentLibrary(root)

    with pytest.raises(AgentLibraryError):
        library.load(path)


@pytest.mark.parametrize(
    "text",
    [
        "not markdown frontmatter",
        "---\nname: Missing close\n# body",
        "---\nname: Missing body\n---\n",
    ],
)
def test_library_rejects_malformed_profiles(tmp_path, text):
    root = tmp_path / "library"
    root.mkdir()
    (root / "bad.md").write_text(text, encoding="utf-8")

    with pytest.raises(AgentLibraryError):
        AgentLibrary(root).load("bad.md")


def test_library_caps_file_size_and_instruction_size(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    (root / "large.md").write_text("x" * 64_001, encoding="utf-8")
    library = AgentLibrary(root)

    with pytest.raises(AgentLibraryError, match="64 KB"):
        library.load("large.md")


def test_library_rejects_too_many_profiles(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    (root / "first.md").write_text("---\nname: first\n---\nbody", encoding="utf-8")
    (root / "second.md").write_text("---\nname: second\n---\nbody", encoding="utf-8")

    with pytest.raises(AgentLibraryError, match="1-profile limit"):
        AgentLibrary(root).list_profiles(limit=1)
