from __future__ import annotations

from typing import Any, Dict

from loguru import logger
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from pai.plugins.base import BasePlugin


class BrowserPlugin(BasePlugin):
    """Browser automation using Playwright."""

    def __init__(
        self,
        *,
        plugin_id: str,
        config: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            plugin_id=plugin_id,
            config=config,
        )

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def get_capabilities(self) -> list[str]:
        return [
            "browser.navigate",
            "browser.click",
            "browser.type",
            "browser.screenshot",
            "browser.get_text",
            "browser.fill_form",
        ]

    async def initialize(self) -> None:
        """Start Playwright and create a browser context."""

        self._playwright = await async_playwright().start()

        browser_name = self.config.get(
            "browser",
            "chromium",
        )

        headless = self.config.get(
            "headless",
            True,
        )

        timeout = self.config.get(
            "timeout",
            30000,
        )

        browser_launcher = getattr(
            self._playwright,
            browser_name,
            None,
        )

        if browser_launcher is None:
            raise ValueError(
                f"Unsupported browser: {browser_name}"
            )

        self._browser = await browser_launcher.launch(
            headless=headless,
        )

        self._context = await self._browser.new_context()

        self._page = await self._context.new_page()

        self._page.set_default_timeout(timeout)

        await super().initialize()

        logger.info(
            f"Browser plugin initialized "
            f"({browser_name}, headless={headless})"
        )

    async def execute(
        self,
        action: str,
        params: Dict[str, Any],
    ) -> Any:

        if self._page is None:
            raise RuntimeError(
                "Browser plugin is not initialized"
            )

        if action == "browser.navigate":
            return await self._navigate(
                params["url"]
            )

        if action == "browser.click":
            return await self._click(
                params["selector"]
            )

        if action == "browser.type":
            return await self._type(
                params["selector"],
                params["text"],
            )

        if action == "browser.screenshot":
            return await self._screenshot(
                params.get("path")
            )

        if action == "browser.get_text":
            return await self._get_text(
                params["selector"]
            )

        if action == "browser.fill_form":
            return await self._fill_form(
                params.get("fields", {})
            )

        raise ValueError(
            f"Unknown browser action: {action}"
        )

    async def _navigate(
        self,
        url: str,
    ) -> Dict[str, Any]:

        response = await self._page.goto(url)

        return {
            "url": self._page.url,
            "title": await self._page.title(),
            "status": (
                response.status
                if response is not None
                else None
            ),
        }

    async def _click(
        self,
        selector: str,
    ) -> Dict[str, Any]:

        await self._page.click(selector)

        return {
            "selector": selector,
            "clicked": True,
        }

    async def _type(
        self,
        selector: str,
        text: str,
    ) -> Dict[str, Any]:

        await self._page.fill(
            selector,
            text,
        )

        return {
            "selector": selector,
            "typed": True,
        }

    async def _screenshot(
        self,
        path: str | None = None,
    ) -> Dict[str, Any]:

        screenshot = await self._page.screenshot(
            path=path,
        )

        return {
            "path": path,
            "size_bytes": len(screenshot),
        }

    async def _get_text(
        self,
        selector: str,
    ) -> Dict[str, Any]:

        text = await self._page.text_content(
            selector,
        )

        return {
            "selector": selector,
            "text": text,
        }

    async def _fill_form(
        self,
        fields: Dict[str, str],
    ) -> Dict[str, Any]:

        for selector, value in fields.items():
            await self._page.fill(
                selector,
                value,
            )

        return {
            "fields_filled": len(fields),
        }

    async def shutdown(self) -> None:
        """Close browser resources."""

        if self._context is not None:
            await self._context.close()
            self._context = None

        if self._browser is not None:
            await self._browser.close()
            self._browser = None

        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

        self._page = None

        await super().shutdown()

        logger.info("Browser plugin shutdown")