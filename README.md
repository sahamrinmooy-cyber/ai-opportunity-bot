# AI Opportunity Bot

A local-first desktop AI assistant for personal chatbot projects and a small set of visible, user-approved browser and workspace actions. It is an early MVP, not a production-ready autonomous agent.

## Requirements

- Python 3.11 or later and [uv](https://docs.astral.sh/uv/)
- Windows, macOS, or Linux with Tkinter available
- An API key from a provider you choose to use chat features

The desktop app and its tests run locally. Chat tests use an injected fake transport and do not need provider credentials or network access.

## Run locally

On Windows PowerShell:

```powershell
uv sync --locked --extra dev --python 3.11
uv run python -m playwright install chromium
uv run opportunity-bot
```

On macOS/Linux, use the same `uv` commands. Some Linux distributions package Tkinter separately.

Run the checks with:

```powershell
uv run --locked ruff check .
uv run --locked pytest
```

## Chatbot projects and credentials

Create a project in the app, choose an API provider, base URL and model, and use **Set API key**. Project settings are stored as local JSON files below the operating system's application-data directory. API keys are stored by `keyring` in the operating system credential store, associated with the local project and provider; they are not written to project files or included in chat error messages.

The provider selector supports OpenAI-compatible `/chat/completions` endpoints and Anthropic's `/v1/messages` API. For OpenAI-compatible services, the default endpoint is OpenAI's API; xAI-compatible services and other compatible gateways can be configured with an endpoint and model that your provider supports. For Anthropic, use its API and a model available to your account. Configure the endpoint, model, and authorized key yourself. This app does not bundle provider access or guarantee compatibility, model availability, or equivalent features for every service. Claude Code, Codex, and other coding products include proprietary services and workflows this app does not reproduce.

Each project keeps up to 20 recent user/assistant messages in memory for multi-turn chat; this conversation is not persisted, and **New chat** clears the active project's context. Before each request, the app asks for confirmation and previews the project instructions, full conversation, current message, and configured endpoint. The provider receives that context and the project's API key. Use only endpoints you trust. HTTPS is required except for loopback endpoints used by local services.

## Review-first coding assistance

Choose a workspace directory, select up to five comma-separated relative file paths, and describe a coding task. Before each provider request, the app shows the selected file contents and asks whether to send them with the task and project instructions to the configured provider. The model must return a strict JSON proposal containing complete file contents; markdown, extra fields, secret files, paths outside the workspace, and changes to existing files not supplied as context are rejected. The app displays a unified diff and asks again before applying it. It checks file hashes immediately before writes and rejects stale proposals instead of overwriting intervening local edits. Changes are written one file at a time; a machine/process failure during a multi-file write can still leave some files applied.

Workspace discovery skips common generated folders, hidden folders, symlinks, and obvious credential/key files and is capped at 500 entries. Explicit context reads are limited to five UTF-8 files of 32 KB each. Proposals are limited to 10 files, 256 KB per file, and 1 MB total. These filters are guardrails, not a substitute for checking what you share with a model provider.

The only built-in project checks are `pytest -q` and `ruff check .`, each with a separate confirmation, a 120-second timeout, and capped displayed output. They run in the selected workspace but are **not sandboxed**: repository tests and configuration can execute arbitrary code as your user and may read or change files available to your account. Review projects before running checks. This is not a safe way to test untrusted repositories.

## Agent profile library

The repository likely matching the requested “details of every bot” is [msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents). Its README describes a roster of Markdown agent personas and converted integrations for several coding tools; these are specialist instructions, not the source code, APIs, credentials, or complete feature sets of Claude Code, Codex, xAI/Grok, or other commercial models. The upstream repository also points to its own Agency Agents desktop app.

To use its profiles here, clone the public repository yourself and choose that local folder with **Choose library**. The app reads a bounded list of Markdown profiles in read-only mode, skips integration/script/hidden directories, and does not run the repository's installer or converter scripts or modify that checkout. **Use profile** requires confirmation, previews the profile's description, loads its bounded instructions into the current project, and records its source path and MIT attribution. Review and save those instructions; they are sent to an AI provider only after the normal per-request consent dialog. The profile collection is not copied into this project, so your locally cloned copy follows upstream updates and its license.

The profile library supplies role/persona prompts; the configured API provider supplies the model. It does not grant access to Claude, Codex, xAI/Grok, or any other service, and it does not make this app feature-equivalent to those proprietary products.

## Browser and PC action boundaries

- Browser automation uses a dedicated persistent Playwright profile owned by this app. It does not attach to or copy cookies from your normal browser profile. Sign-ins made inside the app's profile remain separate from your regular browser profile.
- Opening a URL and writing a workspace file each require an explicit confirmation dialog. Reading is confined to the workspace directory selected in the app. File actions reject absolute paths, traversal, and symlink escapes, and limit text files to 1 MB.
- There is no arbitrary shell execution, general desktop control, web form submission, messaging, downloading, or background action runner. Model proposals cannot directly invoke local actions or commands.
- Treat pages, files, and pasted text as untrusted. Do not rely on their contents to authorize actions or disclose secrets.

## Local data and limitations

Projects and the isolated browser profile are stored under the app's local application-data directory; credentials are held by the OS credential store. If you manually delete a chatbot project's settings file, its saved credential remains; remove unused credentials through the OS credential manager. Browser cookies and site data in the isolated profile persist until you remove that profile data.

The MVP has no GitHub integration, repository installation, cloud service, or third-party deployment. It does not contact repository owners or clients. No credentials are included in the repository. The browser UI currently opens user-entered URLs; it does not inspect or act on page content. It does not reproduce any proprietary model's private implementation or guarantee the same model capabilities; configured providers and their credentials determine response quality.

## Development

The project uses Python, Tkinter, Playwright, and `keyring`. Unit tests use fakes and temporary directories, do not launch a real browser, and make no API requests. CI installs the package and runs Ruff plus pytest.
