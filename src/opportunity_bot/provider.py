"""Mockable chat provider protocol and OpenAI-compatible HTTP implementation."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from opportunity_bot.projects import ChatProject, validate_project

MAX_PROMPT_CHARACTERS = 100_000


class ProviderError(RuntimeError):
    """A safe-to-display provider failure without response bodies or credentials."""


class ChatProvider(Protocol):
    def complete(self, project: ChatProject, prompt: str, api_key: str) -> str: ...


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

    def complete(self, project: ChatProject, prompt: str, api_key: str) -> str:
        validate_project(project)
        if not prompt.strip():
            raise ValueError("Message cannot be empty.")
        if len(prompt) > MAX_PROMPT_CHARACTERS:
            raise ValueError("Message exceeds the 100,000-character limit.")
        if not api_key.strip():
            raise ValueError("Configure an API credential before sending a message.")

        endpoint = project.endpoint.rstrip("/") + "/chat/completions"
        payload = {
            "model": project.model,
            "messages": [
                {"role": "system", "content": project.system_prompt},
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
        opener = build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=self._timeout_seconds) as response:
            return response.read(self._max_response_bytes + 1)
