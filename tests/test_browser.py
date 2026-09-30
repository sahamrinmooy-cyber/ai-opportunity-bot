from pathlib import Path

from opportunity_bot.browser import IsolatedBrowser


def test_browser_uses_only_its_app_owned_persistent_profile(tmp_path, monkeypatch):
    captured = {}

    class Page:
        def goto(self, url, **kwargs):
            captured["url"] = url
            captured["goto_options"] = kwargs

    class Context:
        pages = [Page()]

        def close(self):
            captured["closed"] = True

    class Chromium:
        def launch_persistent_context(self, **kwargs):
            captured["launch_options"] = kwargs
            return Context()

    class Playwright:
        chromium = Chromium()

        def stop(self):
            captured["stopped"] = True

    class PlaywrightManager:
        def start(self):
            return Playwright()

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: PlaywrightManager())
    profile = tmp_path / "app-browser-profile"
    browser = IsolatedBrowser(profile)
    browser.open("https://example.com")
    browser.close()

    launch_options = captured["launch_options"]
    assert Path(launch_options["user_data_dir"]) == profile.resolve()
    assert launch_options["headless"] is False
    assert launch_options["chromium_sandbox"] is True
    assert launch_options["accept_downloads"] is False
    assert captured["url"] == "https://example.com"
    assert captured["closed"] and captured["stopped"]
