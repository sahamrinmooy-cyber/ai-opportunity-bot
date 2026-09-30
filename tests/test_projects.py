import json

import pytest

from opportunity_bot.projects import (
    ChatProject,
    ProjectStore,
    ProjectValidationError,
    validate_project,
)


def test_project_store_round_trip_contains_no_credentials(tmp_path):
    store = ProjectStore(tmp_path)
    project = ChatProject.create("Personal assistant")
    store.save(project)

    assert store.list_projects() == [project]
    serialized = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
    assert "api_key" not in serialized
    assert "secret" not in serialized


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.example.com/v1",
        "https://user:pass@example.com/v1",
        "https://api.example.com/v1?token=secret",
        "file:///etc/passwd",
    ],
)
def test_project_rejects_unsafe_provider_endpoint(endpoint):
    project = ChatProject.create("test")
    with pytest.raises(ProjectValidationError):
        validate_project(ChatProject(**{**project.__dict__, "endpoint": endpoint}))


def test_project_rejects_unknown_config_fields(tmp_path):
    project = ChatProject.create("test")
    store = ProjectStore(tmp_path)
    store.save(project)
    path = tmp_path / f"{project.project_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["api_key"] = "must-not-be-accepted"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ProjectValidationError, match="unsupported settings schema"):
        store.list_projects()


def test_project_rejects_malformed_endpoint_and_non_text_settings():
    project = ChatProject.create("test")
    with pytest.raises(ProjectValidationError, match="invalid authority"):
        validate_project(ChatProject(**{**project.__dict__, "endpoint": "https://[malformed/v1"}))
    with pytest.raises(ProjectValidationError, match="must be text"):
        validate_project(ChatProject(**{**project.__dict__, "model": 17}))
