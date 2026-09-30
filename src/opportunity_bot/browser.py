"""A Playwright session confined to an app-owned persistent browser profile."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class IsolatedBrowser:
    def __init__(self, profile_directory: Path):
        self.profile_directory = profile_directory.resolve()
        self._playwright: Any = None
        self._context: Any = None

    def open(self, url: str) -> None:
        if self._context is None:
            self.profile_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                self.profile_directory.chmod(0o700)
            try:
                from playwright.sync_api import Error as PlaywrightError
                from playwright.sync_api import sync_playwright
            except ImportError as error:
                raise RuntimeError(
                    "Playwright is not installed. Install app dependencies and the "
                    "Chromium browser."
                ) from error
            try:
                manager = sync_playwright().start()
            except PlaywrightError:
                raise RuntimeError("Could not start the isolated browser.") from None
            try:
                context = manager.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_directory),
                    headless=False,
                    chromium_sandbox=True,
                    accept_downloads=False,
                )
                self._playwright = manager
                self._context = context
            except PlaywrightError:
                manager.stop()
                raise RuntimeError("Could not launch the isolated browser.") from None
        try:
            page = self._context.pages[0] if self._context.pages else self._context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightError:
            raise RuntimeError("Isolated browser could not open the requested URL.") from None

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
