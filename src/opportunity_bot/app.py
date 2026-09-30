"""Tkinter desktop entry point."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from opportunity_bot.actions import ActionDeniedError, ActionPreview, ActionService
from opportunity_bot.browser import IsolatedBrowser
from opportunity_bot.credentials import CredentialStoreError, KeyringCredentialStore
from opportunity_bot.projects import ChatProject, ProjectStore
from opportunity_bot.provider import MAX_PROMPT_CHARACTERS, OpenAICompatibleProvider, ProviderError


def _data_directory() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif os.name == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "ai-opportunity-bot"


class DesktopApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Local AI Assistant")
        self.root.geometry("780x760")
        self.store = ProjectStore(_data_directory() / "projects")
        self.credentials = KeyringCredentialStore()
        self.provider = OpenAICompatibleProvider()
        self.browser = IsolatedBrowser(_data_directory() / "browser-profile")
        self.projects = self.store.list_projects()
        self.selected_project: ChatProject | None = None
        self.chat_results: queue.Queue[tuple[bool, str]] = queue.Queue()
        self.workspace: Path | None = None
        self._build_ui()
        self._refresh_projects()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        project_row = ttk.Frame(outer)
        project_row.pack(fill=tk.X)
        ttk.Label(project_row, text="Chatbot project").pack(side=tk.LEFT)
        self.project_choice = ttk.Combobox(project_row, state="readonly")
        self.project_choice.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.project_choice.bind("<<ComboboxSelected>>", self._select_project)
        ttk.Button(project_row, text="New", command=self._new_project).pack(side=tk.LEFT)

        form = ttk.LabelFrame(outer, text="Project settings", padding=8)
        form.pack(fill=tk.X, pady=(10, 0))
        self.name = self._field(form, "Name")
        self.endpoint = self._field(form, "Provider base URL")
        self.model = self._field(form, "Model")
        ttk.Label(form, text="Instructions").grid(row=3, column=0, sticky=tk.NW, pady=3)
        self.system_prompt = ScrolledText(form, height=4, wrap=tk.WORD)
        self.system_prompt.grid(row=3, column=1, sticky=tk.EW, pady=3)
        form.columnconfigure(1, weight=1)
        project_buttons = ttk.Frame(form)
        project_buttons.grid(row=4, column=1, sticky=tk.E, pady=(5, 0))
        ttk.Button(project_buttons, text="Save project", command=self._save_project).pack(
            side=tk.LEFT
        )
        ttk.Button(project_buttons, text="Set API key", command=self._set_api_key).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        chat_frame = ttk.LabelFrame(
            outer, text="Chat (text-only; does not execute actions)", padding=8
        )
        chat_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.chat_log = ScrolledText(chat_frame, height=12, state=tk.DISABLED, wrap=tk.WORD)
        self.chat_log.pack(fill=tk.BOTH, expand=True)
        message_row = ttk.Frame(chat_frame)
        message_row.pack(fill=tk.X, pady=(8, 0))
        self.message = ttk.Entry(message_row)
        self.message.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.message.bind("<Return>", lambda _event: self._send_message())
        self.send_button = ttk.Button(message_row, text="Send", command=self._send_message)
        self.send_button.pack(side=tk.LEFT, padx=(8, 0))

        browser_frame = ttk.LabelFrame(outer, text="Isolated browser", padding=8)
        browser_frame.pack(fill=tk.X, pady=(10, 0))
        self.url = ttk.Entry(browser_frame)
        self.url.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.url.insert(0, "https://example.com")
        ttk.Button(browser_frame, text="Open (approval required)", command=self._open_url).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        file_frame = ttk.LabelFrame(outer, text="Workspace files", padding=8)
        file_frame.pack(fill=tk.X, pady=(10, 0))
        self.workspace_label = ttk.Label(file_frame, text="No workspace selected")
        self.workspace_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(file_frame, text="Choose workspace", command=self._choose_workspace).pack(
            side=tk.LEFT
        )
        self.file_path = ttk.Entry(file_frame)
        self.file_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        self.file_path.insert(0, "notes.txt")
        ttk.Button(file_frame, text="Read", command=self._read_file).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(file_frame, text="Write (approval required)", command=self._write_file).pack(
            side=tk.LEFT, padx=(8, 0)
        )

    @staticmethod
    def _field(parent: ttk.LabelFrame, label: str) -> ttk.Entry:
        row = {"Name": 0, "Provider base URL": 1, "Model": 2}[label]
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=3)
        entry = ttk.Entry(parent)
        entry.grid(row=row, column=1, sticky=tk.EW, pady=3)
        return entry

    def _refresh_projects(self) -> None:
        selected_id = self.selected_project.project_id if self.selected_project else None
        self.projects = self.store.list_projects()
        self.project_choice["values"] = [project.name for project in self.projects]
        if self.projects:
            index = next(
                (i for i, project in enumerate(self.projects) if project.project_id == selected_id),
                0,
            )
            self.project_choice.current(index)
            self._load_project(self.projects[index])
        else:
            self.selected_project = None
            self._clear_project_form()

    def _select_project(self, _event: tk.Event[tk.Misc]) -> None:
        index = self.project_choice.current()
        if 0 <= index < len(self.projects):
            self._load_project(self.projects[index])

    def _load_project(self, project: ChatProject) -> None:
        self.selected_project = project
        self._replace(self.name, project.name)
        self._replace(self.endpoint, project.endpoint)
        self._replace(self.model, project.model)
        self.system_prompt.delete("1.0", tk.END)
        self.system_prompt.insert("1.0", project.system_prompt)
        self._append_chat(f"Selected project: {project.name}")

    def _new_project(self) -> None:
        name = simpledialog.askstring("New chatbot project", "Project name:", parent=self.root)
        if name is None:
            return
        try:
            project = ChatProject.create(name)
            self.store.save(project)
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not create project", str(error), parent=self.root)
            return
        self._refresh_projects()
        index = next(
            (i for i, item in enumerate(self.projects) if item.project_id == project.project_id), -1
        )
        if index >= 0:
            self.project_choice.current(index)
            self._load_project(project)

    def _save_project(self) -> None:
        if self.selected_project is None:
            messagebox.showinfo(
                "Select a project", "Create or select a chatbot project first.", parent=self.root
            )
            return
        project = ChatProject(
            project_id=self.selected_project.project_id,
            name=self.name.get().strip(),
            provider=self.selected_project.provider,
            endpoint=self.endpoint.get().strip(),
            model=self.model.get().strip(),
            system_prompt=self.system_prompt.get("1.0", tk.END).rstrip(),
        )
        try:
            self.store.save(project)
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not save project", str(error), parent=self.root)
            return
        self.selected_project = project
        self._refresh_projects()
        self._append_chat("Project settings saved locally.")

    def _set_api_key(self) -> None:
        if self.selected_project is None:
            messagebox.showinfo(
                "Select a project", "Create or select a chatbot project first.", parent=self.root
            )
            return
        secret = simpledialog.askstring(
            "Provider API key",
            "Enter the API key for this provider. It is stored in the OS credential "
            "store, not the project file.",
            show="*",
            parent=self.root,
        )
        if secret is None:
            return
        try:
            self.credentials.set(self.selected_project.project_id, secret)
        except (CredentialStoreError, ValueError) as error:
            messagebox.showerror("Could not store API key", str(error), parent=self.root)
            return
        messagebox.showinfo(
            "API key stored", "The key was saved in the OS credential store.", parent=self.root
        )

    def _send_message(self) -> None:
        project = self.selected_project
        prompt = self.message.get().strip()
        if project is None or not prompt:
            return
        if len(prompt) > MAX_PROMPT_CHARACTERS:
            messagebox.showerror(
                "Message too long",
                f"Messages must not exceed {MAX_PROMPT_CHARACTERS:,} characters.",
                parent=self.root,
            )
            return
        approved = self._approval_dialog(
            "Approve provider request",
            "Send this project's instructions, your message, and the project's API key "
            f"to {project.endpoint} using model {project.model}?",
            f"Project instructions:\n{project.system_prompt}\n\nMessage:\n{prompt}",
        )
        if not approved:
            return
        try:
            api_key = self.credentials.get(project.project_id)
        except CredentialStoreError as error:
            messagebox.showerror("Could not read API key", str(error), parent=self.root)
            return
        if not api_key:
            messagebox.showinfo(
                "API key required", "Use Set API key before sending a message.", parent=self.root
            )
            return
        self.message.delete(0, tk.END)
        self._append_chat(f"You: {prompt}")
        self.send_button.configure(state=tk.DISABLED)
        threading.Thread(
            target=self._complete_in_background,
            args=(project, prompt, api_key),
            daemon=True,
        ).start()
        self.root.after(100, self._poll_chat_result)

    def _complete_in_background(self, project: ChatProject, prompt: str, api_key: str) -> None:
        try:
            self.chat_results.put((True, self.provider.complete(project, prompt, api_key)))
        except (ProviderError, ValueError, OSError):
            self.chat_results.put(
                (False, "Could not complete the request. Check the endpoint and try again.")
            )

    def _poll_chat_result(self) -> None:
        try:
            succeeded, result = self.chat_results.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_chat_result)
            return
        self.send_button.configure(state=tk.NORMAL)
        self._append_chat(f"Assistant: {result}" if succeeded else f"Provider error: {result}")

    def _action_service(self) -> ActionService:
        return ActionService(self.workspace, self._approve_action, self.browser.open)

    def _approve_action(self, preview: ActionPreview) -> bool:
        return self._approval_dialog(
            "Approve action",
            f"{preview.description}\n\nThis action is consequential.",
            preview.details,
        )

    def _approval_dialog(self, title: str, description: str, details: str) -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.geometry("620x420")
        dialog.minsize(480, 300)
        ttk.Label(dialog, text=description, wraplength=580, justify=tk.LEFT).pack(
            fill=tk.X, padx=12, pady=(12, 8)
        )
        if details:
            preview = ScrolledText(dialog, height=14, wrap=tk.WORD)
            preview.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
            preview.insert("1.0", details)
            preview.configure(state=tk.DISABLED)
        approved = {"value": False}

        def close(approve: bool = False) -> None:
            approved["value"] = approve
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.pack(fill=tk.X, padx=12, pady=12)
        ttk.Button(buttons, text="Cancel", command=close).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Approve", command=lambda: close(True)).pack(
            side=tk.RIGHT, padx=(0, 8)
        )
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.grab_set()
        dialog.wait_window()
        return approved["value"]

    def _open_url(self) -> None:
        try:
            result = self._action_service().execute("open_url", {"url": self.url.get().strip()})
        except (ActionDeniedError, OSError, RuntimeError) as error:
            messagebox.showerror("Browser action failed", str(error), parent=self.root)
            return
        self._append_chat(result)

    def _choose_workspace(self) -> None:
        directory = filedialog.askdirectory(
            parent=self.root, title="Select an allowed workspace directory"
        )
        if directory:
            self.workspace = Path(directory).resolve()
            self.workspace_label.configure(text=str(self.workspace))

    def _read_file(self) -> None:
        try:
            content = self._action_service().execute(
                "read_workspace_file", {"path": self.file_path.get().strip()}
            )
        except (ActionDeniedError, OSError, UnicodeError) as error:
            messagebox.showerror("Could not read workspace file", str(error), parent=self.root)
            return
        self._show_text("Workspace file content", content)

    def _write_file(self) -> None:
        if self.workspace is None:
            messagebox.showerror(
                "Workspace required",
                "Choose a workspace directory before writing a file.",
                parent=self.root,
            )
            return
        content = simpledialog.askstring(
            "Write workspace file",
            "Text content to write (new and existing files both require confirmation):",
            parent=self.root,
        )
        if content is None:
            return
        try:
            result = self._action_service().execute(
                "write_workspace_file",
                {"path": self.file_path.get().strip(), "content": content},
            )
        except (ActionDeniedError, OSError, UnicodeError) as error:
            messagebox.showerror("Could not write workspace file", str(error), parent=self.root)
            return
        self._append_chat(result)

    def _show_text(self, title: str, content: str) -> None:
        window = tk.Toplevel(self.root)
        window.title(title)
        text = ScrolledText(window, width=80, height=24, wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        text.insert("1.0", content)
        text.configure(state=tk.DISABLED)

    def _append_chat(self, text: str) -> None:
        self.chat_log.configure(state=tk.NORMAL)
        self.chat_log.insert(tk.END, text + "\n\n")
        self.chat_log.see(tk.END)
        self.chat_log.configure(state=tk.DISABLED)

    @staticmethod
    def _replace(entry: ttk.Entry, value: str) -> None:
        entry.delete(0, tk.END)
        entry.insert(0, value)

    def _clear_project_form(self) -> None:
        for entry in (self.name, self.endpoint, self.model):
            self._replace(entry, "")
        self.system_prompt.delete("1.0", tk.END)

    def close(self) -> None:
        try:
            self.browser.close()
        finally:
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    DesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
