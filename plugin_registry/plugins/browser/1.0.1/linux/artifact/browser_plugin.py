"""browser plugin — Playwright-backed web automation."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class BrowserPlugin:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or {}
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()
        self._out_dir = Path(
            self.config.get("output_dir", "/tmp/pai-browser")
        )
        self._out_dir.mkdir(parents=True, exist_ok=True)

    async def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        async with self._lock:
            if self._browser is not None:
                return
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

    async def shutdown(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def invoke(self, capability: str, parameters: Dict[str, Any]) -> Any:
        if capability == "browser.navigate":
            return await self._navigate(parameters)
        if capability == "browser.fill_form":
            return await self._fill_form(parameters)
        if capability == "browser.book":
            return await self._book(parameters)
        raise ValueError(f"unknown capability: {capability}")

    # ----- internal -----

    def _storage_path(self, user_id: str) -> Path:
        return self._out_dir / f"storage_{user_id}.json"

    def _context_kwargs(self, user_id: str) -> Dict[str, Any]:
        path = self._storage_path(user_id)
        return {"storage_state": str(path)} if path.exists() else {}

    async def _save_state(self, ctx, user_id: str) -> None:
        try:
            await ctx.storage_state(path=self._storage_path(user_id))
        except Exception:
            logger.exception("storage_state save failed for %s", user_id)

    async def _navigate(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("user_id", "default")
        await self._ensure_browser()
        async with self._lock:
            ctx = await self._browser.new_context(
                **self._context_kwargs(user_id)
            )
            page = await ctx.new_page()
            try:
                resp = await page.goto(
                    params["url"],
                    wait_until=params.get("wait_until", "domcontentloaded"),
                    timeout=30_000,
                )
                title = await page.title()
                text = await page.inner_text("body")
                return {
                    "ok": True,
                    "url": page.url,
                    "status": resp.status if resp else None,
                    "title": title,
                    "text": text[:20_000],
                }
            finally:
                await self._save_state(ctx, user_id)
                await ctx.close()

    async def _fill_form(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("user_id", "default")
        await self._ensure_browser()
        async with self._lock:
            ctx = await self._browser.new_context(
                **self._context_kwargs(user_id)
            )
            page = await ctx.new_page()
            start_url = params["url"]
            try:
                await page.goto(
                    start_url,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )

                # Fill — collect per-field errors instead of aborting on
                # the first bad selector.
                fill_errors: Dict[str, str] = {}
                for selector, value in params["fields"].items():
                    try:
                        # 5s is plenty for any real form field. Bad selectors
                        # should fail fast — 30s default hides problems and
                        # races against the caller's --timeout.
                        await page.fill(selector, str(value), timeout=5_000)
                    except Exception as exc:
                        fill_errors[selector] = str(exc)[:200]

                # Submit — capture whether navigation happened
                submit_info: Optional[Dict[str, Any]] = None
                if params.get("submit_selector"):
                    url_before = page.url
                    navigated = False
                    try:
                        # Native form submit → full navigation
                        async with page.expect_navigation(timeout=15_000):
                            await page.click(params["submit_selector"])
                        navigated = True
                    except Exception:
                        # No navigation — SPA / type="button" / blocked
                        try:
                            await page.wait_for_load_state(
                                "networkidle", timeout=5_000
                            )
                        except Exception:
                            pass
                    submit_info = {
                        "clicked": True,
                        "navigated": navigated,
                        "url_before": url_before,
                        "url_after": page.url,
                    }

                # Screenshot (opt-in)
                shot = None
                if params.get("screenshot"):
                    shot = str(
                        self._out_dir / f"form_{int(time.time())}.png"
                    )
                    await page.screenshot(path=shot, full_page=True)

                # Diagnostic payload — this is what tells you what happened
                title = await page.title()
                try:
                    text = await page.inner_text("body")
                except Exception:
                    text = ""

                return {
                    "ok": True,
                    "url": page.url,
                    "url_changed": page.url != start_url,
                    "title": title,
                    "text": text[:20_000],
                    "screenshot": shot,
                    "fill_errors": fill_errors or None,
                    "submit": submit_info,
                }
            finally:
                await self._save_state(ctx, user_id)
                await ctx.close()

    async def _book(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("user_id", "default")
        await self._ensure_browser()
        async with self._lock:
            ctx = await self._browser.new_context(
                **self._context_kwargs(user_id)
            )
            page = await ctx.new_page()
            try:
                await page.goto(
                    params["start_url"],
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                for step in params["steps"]:
                    action = step["action"]
                    if action == "fill":
                        await page.fill(
                            step["selector"], str(step["value"])
                        )
                    elif action == "click":
                        await page.click(step["selector"])
                        await page.wait_for_load_state(
                            "networkidle", timeout=30_000
                        )
                    elif action == "select":
                        await page.select_option(
                            step["selector"], step["value"]
                        )
                    elif action == "wait":
                        await asyncio.sleep(float(step.get("seconds", 1)))
                body = await page.inner_text("body")
                return {
                    "ok": True,
                    "url": page.url,
                    "text": body[:20_000],
                }
            finally:
                await self._save_state(ctx, user_id)
                await ctx.close()

def create_plugin(config: Dict[str, Any]) -> BrowserPlugin:
    """Entry point for the device agent.

    Called once at load time. Return any object exposing:

        async def invoke(capability: str, params: dict) -> Any
        async def shutdown() -> None      # optional
    """
    return BrowserPlugin(config)