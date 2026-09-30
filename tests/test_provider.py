import json
from urllib.request import Request

import pytest

from opportunity_bot.projects import ChatProject, ProjectValidationError
from opportunity_bot.provider import (
    AnthropicMessagesProvider,
    ChatMessage,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderRouter,
    _NoRedirectHandler,
)


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


def test_openai_compatible_provider_includes_validated_conversation_history():
    requests = []

    def fake_transport(request):
        requests.append(request)
        return b'{"choices":[{"message":{"content":"continued"}}]}'

    history = (
        ChatMessage("user", "Earlier question"),
        ChatMessage("assistant", "Earlier response"),
    )
    OpenAICompatibleProvider(transport=fake_transport).complete(
        ChatProject.create("test"), "Follow-up", "test-key", history
    )

    messages = json.loads(requests[0].data)["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[1]["content"] == "Earlier question"
    assert messages[-1]["content"] == "Follow-up"


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


@pytest.mark.parametrize(
    "history",
    [
        (ChatMessage("tool", "not allowed"),),
        tuple(ChatMessage("user", "x") for _ in range(21)),
        [ChatMessage("user", "mutable list")],
    ],
)
def test_provider_rejects_invalid_or_oversized_conversation_history(history):
    provider = OpenAICompatibleProvider(transport=lambda _request: b"{}")

    with pytest.raises(ValueError, match="Conversation"):
        provider.complete(ChatProject.create("test"), "Hi", "local-test-key", history)


def test_anthropic_provider_uses_messages_api_and_only_returns_text_blocks():
    requests = []

    def fake_transport(request):
        requests.append(request)
        return json.dumps(
            {
                "content": [
                    {"type": "text", "text": "First"},
                    {"type": "tool_use", "id": "ignored", "name": "unsupported"},
                    {"type": "text", "text": "Second"},
                ]
            }
        ).encode()

    project = ChatProject.create("Claude-compatible")
    project = ChatProject(
        **{**project.__dict__, "provider": "anthropic", "endpoint": "https://api.anthropic.com/v1"}
    )
    response = AnthropicMessagesProvider(transport=fake_transport).complete(
        project,
        "Review this code",
        "local-test-key",
        (ChatMessage("user", "Earlier"), ChatMessage("assistant", "Response")),
    )

    assert response == "First\nSecond"
    request = requests[0]
    assert request.full_url == "https://api.anthropic.com/v1/messages"
    assert request.get_header("X-api-key") == "local-test-key"
    assert request.get_header("Anthropic-version") == "2023-06-01"
    payload = json.loads(request.data)
    assert payload["model"] == project.model
    assert payload["system"] == project.system_prompt
    assert payload["messages"] == [
        {"role": "user", "content": "Earlier"},
        {"role": "assistant", "content": "Response"},
        {"role": "user", "content": "Review this code"},
    ]
    assert payload["max_tokens"] == 4096


def test_provider_router_selects_native_provider_and_rejects_unknown_types():
    called = []

    class FakeProvider:
        def complete(self, project, prompt, api_key, history=()):
            called.append((project.provider, prompt, api_key, history))
            return "Claude answer"

    router = ProviderRouter(anthropic=FakeProvider())
    project = ChatProject.create("test")
    anthropic = ChatProject(**{**project.__dict__, "provider": "anthropic"})

    assert router.complete(anthropic, "Hello", "credential") == "Claude answer"
    assert called == [("anthropic", "Hello", "credential", ())]
    with pytest.raises(ProjectValidationError):
        router.complete(ChatProject(**{**project.__dict__, "provider": "unrecognized"}), "Hi", "k")
