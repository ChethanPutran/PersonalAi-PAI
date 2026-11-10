"""browser plugin — Playwright-backed web automation."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict

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
            try:
                await page.goto(
                    params["url"],
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                for selector, value in params["fields"].items():
                    await page.fill(selector, str(value))
                if params.get("submit_selector"):
                    await page.click(params["submit_selector"])
                    await page.wait_for_load_state(
                        "networkidle", timeout=30_000
                    )
                shot = None
                if params.get("screenshot"):
                    shot = str(
                        self._out_dir / f"form_{int(time.time())}.png"
                    )
                    await page.screenshot(path=shot, full_page=True)
                return {
                    "ok": True,
                    "url": page.url,
                    "screenshot": shot,
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
