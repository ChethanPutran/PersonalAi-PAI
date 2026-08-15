"""Browser automation plugin using Playwright."""

from typing import Dict, Any, List
from loguru import logger
from playwright.async_api import async_playwright

from pai.plugins.base_plugin import BasePlugin


class BrowserPlugin(BasePlugin):
    """Browser automation plugin."""
    
    def __init__(self):
        super().__init__()
        self.name = "browser"
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
    
    async def initialize(self) -> None:
        """Initialize browser automation."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=False)
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        logger.info("Browser plugin initialized")
    
    def get_capabilities(self) -> List[str]:
        """Get plugin capabilities."""
        return [
            "browser.navigate",
            "browser.click",
            "browser.type",
            "browser.screenshot",
            "browser.get_text",
            "browser.fill_form"
        ]

    async def check_permissions(self, action: str) -> bool:
        """Check if the plugin has permission to perform the given action."""
        # For simplicity, allow all actions. Implement your own permission logic here.
        return True


    async def start(self) -> None:
        """Start the plugin."""
        self._running = True
        logger.info("Browser plugin started")

    async def handle_event(self, event: str, data: Dict[str, Any]) -> None:
        """Handle an event published on the event bus."""
        logger.info(f"Browser plugin received event: {event} with data: {data}")
        # Implement your event handling logic here
        
    async def execute(self, action: str, params: Dict[str, Any]) -> Any:
        """Execute a browser action."""
        if action == "browser.navigate":
            return await self._navigate(params.get("url"))
        elif action == "browser.click":
            return await self._click(params.get("selector"))
        elif action == "browser.type":
            return await self._type(params.get("selector"), params.get("text"))
        elif action == "browser.screenshot":
            return await self._screenshot(params.get("path"))
        elif action == "browser.get_text":
            return await self._get_text(params.get("selector"))
        elif action == "browser.fill_form":
            return await self._fill_form(params.get("fields", {}))
        else:
            raise ValueError(f"Unknown action: {action}")
    
    async def _navigate(self, url: str) -> Dict[str, Any]:
        """Navigate to a URL."""
        await self._page.goto(url)
        return {"url": url, "title": await self._page.title()}
    
    async def _click(self, selector: str) -> Dict[str, Any]:
        """Click an element."""
        await self._page.click(selector)
        return {"selector": selector, "clicked": True}
    
    async def _type(self, selector: str, text: str) -> Dict[str, Any]:
        """Type text into an element."""
        await self._page.fill(selector, text)
        return {"selector": selector, "text": text}
    
    async def _screenshot(self, path: str = None) -> bytes:
        """Take a screenshot."""
        screenshot = await self._page.screenshot(path=path)
        return screenshot
    
    async def _get_text(self, selector: str) -> str:
        """Get text from an element."""
        text = await self._page.text_content(selector)
        return text
    
    async def _fill_form(self, fields: Dict[str, str]) -> Dict[str, Any]:
        """Fill multiple form fields."""
        for selector, value in fields.items():
            await self._page.fill(selector, value)
        return {"fields_filled": len(fields)}
    
    async def shutdown(self) -> None:
        """Shutdown browser."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser plugin shutdown")