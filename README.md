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

Create a project in the app, choose a provider base URL and model, and use **Set API key**. Project settings are stored as local JSON files below the operating system's application-data directory. API keys are stored by `keyring` in the operating system credential store, associated with the local project ID; they are not written to project files or included in chat error messages.

The provider adapter uses the OpenAI-compatible `/chat/completions` API shape. The default endpoint is OpenAI's API. If you choose another legitimate compatible provider, set its endpoint and model yourself. For example, an xAI-compatible endpoint can be configured when you have authorized credentials and a model name from that provider. This app does not bundle or provide provider access, reproduce proprietary branding, or guarantee compatibility with every service.

Before each chat request, the app asks for confirmation and shows the configured endpoint; the request sends both the message and that project's API key to that endpoint. Use only endpoints you trust. HTTPS is required except for loopback endpoints used by local services. The app does not execute model tool calls: chat responses are text, separate from the local action controls.

## Browser and PC action boundaries

- Browser automation uses a dedicated persistent Playwright profile owned by this app. It does not attach to or copy cookies from your normal browser profile. Sign-ins made inside the app's profile remain separate from your regular browser profile.
- Opening a URL and writing a workspace file each require an explicit confirmation dialog. Reading is confined to the workspace directory selected in the app. File actions reject absolute paths, traversal, and symlink escapes, and limit text files to 1 MB.
- There is no arbitrary shell execution, general desktop control, web form submission, messaging, downloading, or background action runner. The text chatbot cannot invoke local actions.
- Treat pages, files, and pasted text as untrusted. Do not rely on their contents to authorize actions or disclose secrets.

## Local data and limitations

Projects and the isolated browser profile are stored under the app's local application-data directory; credentials are held by the OS credential store. If you manually delete a chatbot project's settings file, its saved credential remains; remove unused credentials through the OS credential manager. Browser cookies and site data in the isolated profile persist until you remove that profile data.

The MVP has no GitHub integration, repository installation, cloud service, or third-party deployment. It does not contact repository owners or clients. No credentials are included in the repository. The browser UI currently opens user-entered URLs; it does not inspect or act on page content.

## Development

The project uses Python, Tkinter, Playwright, and `keyring`. Unit tests use fakes and temporary directories, do not launch a real browser, and make no API requests. CI installs the package and runs Ruff plus pytest.
