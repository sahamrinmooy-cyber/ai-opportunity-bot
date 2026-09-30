import json
from urllib.request import Request

import pytest

from opportunity_bot.projects import ChatProject
from opportunity_bot.provider import OpenAICompatibleProvider, ProviderError, _NoRedirectHandler


def test_provider_uses_injected_transport_and_never_returns_tool_calls():
    requests = []

    def fake_transport(request: Request) -> bytes:
        requests.append(request)
        return json.dumps(
            {"choices": [{"message": {"content": "Hello", "tool_calls": [{"id": "ignored"}]}}]}
        ).encode()

    project = ChatProject.create("test")
    response = OpenAICompatibleProvider(transport=fake_transport).complete(
        project, "Hi", "local-test-key"
    )

    assert response == "Hello"
    assert requests[0].full_url == "https://api.openai.com/v1/chat/completions"
    assert requests[0].get_header("Authorization") == "Bearer local-test-key"
    body = json.loads(requests[0].data)
    assert [message["role"] for message in body["messages"]] == ["system", "user"]


def test_provider_redacts_http_error_response_details():
    from urllib.error import HTTPError

    def failing_transport(_request: Request) -> bytes:
        raise HTTPError("https://example.com", 401, "unauthorized", {}, None)

    with pytest.raises(ProviderError, match="HTTP 401"):
        OpenAICompatibleProvider(transport=failing_transport).complete(
            ChatProject.create("test"), "Hi", "not-for-errors"
        )


def test_provider_never_follows_redirects_with_authorization_header():
    handler = _NoRedirectHandler()
    request = Request("https://provider.example/v1/chat/completions")

    assert (
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://untrusted.example/collect",
        )
        is None
    )


def test_provider_rejects_oversized_responses_without_network():
    def transport(_request: Request) -> bytes:
        return b"x" * (2_000_001)

    provider = OpenAICompatibleProvider(transport=transport)

    with pytest.raises(ProviderError, match="2 MB limit"):
        provider.complete(ChatProject.create("test"), "Hi", "local-test-key")


def test_provider_rejects_oversized_prompt_before_transport():
    calls = []

    def transport(request: Request) -> bytes:
        calls.append(request)
        return b"{}"

    with pytest.raises(ValueError, match="100,000-character limit"):
        OpenAICompatibleProvider(transport=transport).complete(
            ChatProject.create("test"), "x" * 100_001, "local-test-key"
        )
    assert not calls
