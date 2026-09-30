"""Mockable chat provider protocol and OpenAI-compatible HTTP implementation."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from opportunity_bot.projects import ChatProject, validate_project

MAX_PROMPT_CHARACTERS = 100_000
MAX_HISTORY_MESSAGES = 20


class ProviderError(RuntimeError):
    """A safe-to-display provider failure without response bodies or credentials."""


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


def validate_chat_messages(
    project: ChatProject,
    prompt: str,
    history: tuple[ChatMessage, ...],
) -> None:
    if not prompt.strip():
        raise ValueError("Message cannot be empty.")
    if not isinstance(history, tuple):
        raise ValueError("Conversation history must be an immutable message tuple.")
    if len(history) > MAX_HISTORY_MESSAGES:
        raise ValueError(f"Conversation is limited to {MAX_HISTORY_MESSAGES} prior messages.")
    if any(
        not isinstance(message, ChatMessage)
        or message.role not in {"user", "assistant"}
        or not isinstance(message.content, str)
        for message in history
    ):
        raise ValueError("Conversation history may contain only user and assistant text.")
    total_characters = (
        len(project.system_prompt) + len(prompt) + sum(len(message.content) for message in history)
    )
    if total_characters > MAX_PROMPT_CHARACTERS:
        raise ValueError("Message and conversation history exceed the 100,000-character limit.")


class ChatProvider(Protocol):
    def complete(
        self,
        project: ChatProject,
        prompt: str,
        api_key: str,
        history: tuple[ChatMessage, ...] = (),
    ) -> str: ...


class _NoRedirectProvider:
    @staticmethod
    def send(
        request: Request,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        opener = build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout_seconds) as response:
            return response.read(max_response_bytes + 1)


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class OpenAICompatibleProvider:
    _max_response_bytes = 2_000_000

    def __init__(
        self,
        transport: Callable[[Request], bytes] | None = None,
        timeout_seconds: float = 45,
    ):
        self._transport = transport or self._send
        self._timeout_seconds = timeout_seconds

    def complete(
        self,
        project: ChatProject,
        prompt: str,
        api_key: str,
        history: tuple[ChatMessage, ...] = (),
    ) -> str:
        validate_project(project)
        if project.provider != "openai-compatible":
            raise ValueError("OpenAI-compatible chat requires an openai-compatible project.")
        validate_chat_messages(project, prompt, history)
        if not api_key.strip():
            raise ValueError("Configure an API credential before sending a message.")

        endpoint = project.endpoint.rstrip("/") + "/chat/completions"
        payload = {
            "model": project.model,
            "messages": [
                {"role": "system", "content": project.system_prompt},
                *({"role": message.role, "content": message.content} for message in history),
                {"role": "user", "content": prompt},
            ],
        }
        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = self._transport(request)
            if len(response) > self._max_response_bytes:
                raise ProviderError("Provider response exceeded the 2 MB limit.")
            decoded = json.loads(response)
            content = decoded["choices"][0]["message"]["content"]
        except HTTPError as error:
            error.close()
            raise ProviderError(f"Provider returned HTTP {error.code}.") from None
        except (URLError, OSError, TimeoutError):
            raise ProviderError("Could not connect to the configured provider endpoint.") from None
        except (KeyError, IndexError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError("Provider returned an invalid chat response.") from None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("Provider returned no text response.")
        return content

    def _send(self, request: Request) -> bytes:
        return _NoRedirectProvider.send(request, self._timeout_seconds, self._max_response_bytes)


class AnthropicMessagesProvider:
    _max_response_bytes = 2_000_000

    def __init__(
        self,
        transport: Callable[[Request], bytes] | None = None,
        timeout_seconds: float = 45,
    ):
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    def complete(
        self,
        project: ChatProject,
        prompt: str,
        api_key: str,
        history: tuple[ChatMessage, ...] = (),
    ) -> str:
        validate_project(project)
        if project.provider != "anthropic":
            raise ValueError("Anthropic Messages requires an Anthropic project.")
        validate_chat_messages(project, prompt, history)
        if not api_key.strip():
            raise ValueError("Configure an API credential before sending a message.")

        payload = {
            "model": project.model,
            "max_tokens": 4096,
            "system": project.system_prompt,
            "messages": [
                *({"role": message.role, "content": message.content} for message in history),
                {"role": "user", "content": prompt},
            ],
        }
        request = Request(
            project.endpoint.rstrip("/") + "/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = (
                self._transport(request)
                if self._transport is not None
                else _NoRedirectProvider.send(
                    request, self._timeout_seconds, self._max_response_bytes
                )
            )
            if len(response) > self._max_response_bytes:
                raise ProviderError("Provider response exceeded the 2 MB limit.")
            decoded = json.loads(response)
            blocks = decoded["content"]
            text_blocks = [
                block["text"]
                for block in blocks
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ]
        except HTTPError as error:
            error.close()
            raise ProviderError(f"Provider returned HTTP {error.code}.") from None
        except (URLError, OSError, TimeoutError):
            raise ProviderError("Could not connect to the configured provider endpoint.") from None
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError("Provider returned an invalid chat response.") from None
        content = "\n".join(text_blocks)
        if not content.strip():
            raise ProviderError("Provider returned no text response.")
        return content


class ProviderRouter:
    def __init__(
        self,
        openai_compatible: ChatProvider | None = None,
        anthropic: ChatProvider | None = None,
    ):
        self._providers = {
            "openai-compatible": (
                openai_compatible if openai_compatible is not None else OpenAICompatibleProvider()
            ),
            "anthropic": anthropic if anthropic is not None else AnthropicMessagesProvider(),
        }

    def complete(
        self,
        project: ChatProject,
        prompt: str,
        api_key: str,
        history: tuple[ChatMessage, ...] = (),
    ) -> str:
        validate_project(project)
        try:
            provider = self._providers[project.provider]
        except KeyError:
            raise ProviderError("The configured provider is not supported.") from None
        return provider.complete(project, prompt, api_key, history)
