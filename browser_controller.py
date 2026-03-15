"""
Browser Controller — Synchronous Playwright wrapper.
Persistent context, visible browser, Set-of-Marks element IDs.
"""

import os
import json
import time
import logging
import re
from typing import Optional
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright, BrowserContext, Page, Playwright

import config

logger = logging.getLogger(__name__)

# Captcha detection keywords
_CAPTCHA_SIGNALS = [
    "captcha", "recaptcha", "hcaptcha", "cf-challenge",
    "challenge-form", "i-am-not-a-robot", "verify you are human",
]


def _clean(text: str) -> str:
    """Strip lone UTF-16 surrogates from strings returned by page.evaluate().
    Playwright can return surrogate chars from pages with broken encoding
    (email clients, CIS sites). They crash json serialization downstream."""
    if not text:
        return text
    return re.sub(r'[\ud800-\udfff]', '\ufffd', text)


@dataclass
class ActionResult:
    success: bool
    message: str
    error: Optional[str] = None

    def __str__(self) -> str:
        if self.success:
            return f"OK: {self.message}"
        return f"ERROR: {self.message}" + (f" — {self.error}" if self.error else "")


@dataclass
class PageState:
    url: str = ""
    title: str = ""
    elements: list = field(default_factory=list)
    page_text: str = ""
    screenshot_path: Optional[str] = None
    has_captcha: bool = False


class BrowserController:
    """
    Synchronous Playwright wrapper.
    Key design decisions:
    - Persistent context (keeps login sessions across tasks)
    - No slow_mo — speed is handled by explicit waits only where needed
    - Screenshots for both OpenAI and Claude vision models
    - Automatic recovery from stale page/context
    - press_key support for autocomplete/dropdown interactions
    """

    NAV_TIMEOUT = 20_000       # 20s for page navigation
    ACTION_TIMEOUT = 8_000     # 8s for clicks/types
    ACTION_WAIT = 300          # ms fallback wait after click/type
    NETWORKIDLE_TIMEOUT = 2000 # ms to wait for network idle after nav

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._js_script: str = self._load_js()
        self._ids_stamped: bool = False  # track whether current DOM has agent IDs

    def _load_js(self) -> str:
        js_path = os.path.join(os.path.dirname(__file__), "js", "extract_elements.js")
        with open(js_path, "r", encoding="utf-8") as f:
            return f.read()

    # ------------------------------------------------------------------ #
    # Launch & Lifecycle                                                   #
    # ------------------------------------------------------------------ #

    def launch(self) -> None:
        """Launch visible browser with persistent context."""
        if self._playwright:
            try:
                self.close()
            except Exception:
                pass

        self._playwright = sync_playwright().start()

        launch_args = {
            "user_data_dir": config.USER_DATA_DIR,
            "headless": config.BROWSER_HEADLESS,
            "slow_mo": 0,
            "viewport": {
                "width": config.BROWSER_VIEWPORT_WIDTH,
                "height": config.BROWSER_VIEWPORT_HEIGHT,
            },
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-software-rasterizer",
            ],
            "ignore_default_args": ["--enable-automation"],
        }

        if config.BROWSER_CHANNEL:
            launch_args["channel"] = config.BROWSER_CHANNEL

        self._context = self._playwright.chromium.launch_persistent_context(**launch_args)
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.on("dialog", lambda d: d.accept())
        self._ids_stamped = False
        logger.info("Browser launched")

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as e:
            logger.warning(f"Error closing browser: {e}")
        finally:
            self._page = None
            self._context = None
            self._playwright = None
            self._ids_stamped = False

    # ------------------------------------------------------------------ #
    # Page Recovery                                                        #
    # ------------------------------------------------------------------ #

    @property
    def page(self) -> Page:
        return self._ensure_page()

    def _ensure_page(self) -> Page:
        """Return a valid page, recovering from stale context/page if needed."""
        if self._context:
            try:
                self._context.pages  # probe
            except Exception:
                logger.warning("Context stale — relaunching browser")
                self.launch()

        if self._page:
            try:
                if not self._page.is_closed():
                    self._page.url  # probe
                    return self._page
            except Exception:
                self._page = None

        try:
            pages = self._context.pages
            if pages:
                self._page = pages[-1]
                self._ids_stamped = False
                return self._page
        except Exception:
            self.launch()
            return self._page

        self._page = self._context.new_page()
        self._page.on("dialog", lambda d: d.accept())
        self._ids_stamped = False
        return self._page

    # ------------------------------------------------------------------ #
    # Navigation                                                           #
    # ------------------------------------------------------------------ #

    def goto(self, url: str, retries: int = 2) -> ActionResult:
        """Navigate to a URL. Retries on transient network errors, but not DNS failures."""
        for attempt in range(retries + 1):
            try:
                p = self.page
                p.goto(url, timeout=self.NAV_TIMEOUT, wait_until="domcontentloaded")
                try:
                    p.wait_for_load_state("networkidle", timeout=self.NETWORKIDLE_TIMEOUT)
                except Exception:
                    pass  # networkidle not always reachable (websockets, long-poll)
                self._ids_stamped = False
                return ActionResult(success=True, message=f"Navigated to {p.url}")
            except Exception as e:
                err = str(e)
                # DNS failure — retrying won't help, the domain simply doesn't exist or
                # isn't reachable. Return immediately with a clear hint so the agent
                # falls back to searching instead of wasting retries.
                if "ERR_NAME_NOT_RESOLVED" in err or "ERR_NAME_RESOLUTION_FAILED" in err:
                    return ActionResult(
                        False,
                        f"Domain not found: {url}",
                        "DNS resolution failed — the URL may be wrong or unavailable in this region. "
                        "Try a different domain (e.g. .ru instead of .ua) or search Google for the correct URL.",
                    )
                # Transient errors — worth retrying
                if attempt < retries and ("Timeout" in err or "net::" in err or "Protocol error" in err):
                    logger.warning(f"Navigation attempt {attempt+1} failed ({err[:80]}), retrying...")
                    self._page = None
                    time.sleep(1)
                    continue
                if "Timeout" in err:
                    return ActionResult(False, "Navigation timed out", "Page took too long to load")
                return ActionResult(False, "Navigation failed", err[:200])

    def go_back(self) -> ActionResult:
        try:
            self.page.go_back(timeout=self.NAV_TIMEOUT)
            try:
                self.page.wait_for_load_state("networkidle", timeout=self.NETWORKIDLE_TIMEOUT)
            except Exception:
                pass
            self._ids_stamped = False
            return ActionResult(True, f"Went back to {self.page.url}")
        except Exception as e:
            return ActionResult(False, "Go back failed", str(e)[:200])

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def click_element(self, element_id: int) -> ActionResult:
        """
        Click element by its Set-of-Marks ID.
        Fallback chain:
          1. Normal click — with new-tab detection (Google search results open new tabs)
          2. Force click — bypasses overlay/intercept actionability checks
          3. JS click — works even if element is behind z-index or off-screen
        """
        self._ensure_ids()
        el = self.page.locator(f'[data-agent-id="{element_id}"]')

        if el.count() == 0:
            return ActionResult(False, f"Element {element_id} not found",
                                "IDs may have changed — get a fresh page state first.")

        try:
            el.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass  # best-effort, continue anyway

        # --- Strategy 1: normal click with new-tab detection ---
        try:
            with self._context.expect_page(timeout=2500) as new_page_info:
                el.click(timeout=self.ACTION_TIMEOUT)
            # A new tab opened — switch focus to it
            new_page = new_page_info.value
            new_page.wait_for_load_state("domcontentloaded", timeout=self.NAV_TIMEOUT)
            try:
                new_page.wait_for_load_state("networkidle", timeout=self.NETWORKIDLE_TIMEOUT)
            except Exception:
                pass
            self._page = new_page
            self._page.on("dialog", lambda d: d.accept())
            self._ids_stamped = False
            return ActionResult(True, f"Clicked element {element_id} — switched to new tab ({new_page.url[:80]})")
        except Exception as e:
            err = str(e)
            # expect_page timed out = no new tab opened = normal same-page click
            if "expect_page" in err or ("Timeout" in err and "2500" in err):
                # Click went through fine, just no new tab
                self._ids_stamped = False
                try:
                    self.page.wait_for_load_state("networkidle", timeout=self.NETWORKIDLE_TIMEOUT)
                except Exception:
                    self.page.wait_for_timeout(self.ACTION_WAIT)
                return ActionResult(True, f"Clicked element {element_id}")

            # Click itself timed out or was intercepted — try force click
            logger.debug(f"Normal click failed ({err[:80]}), trying force click")

        # --- Strategy 2: force click ---
        try:
            el.click(timeout=self.ACTION_TIMEOUT, force=True)
            self._ids_stamped = False
            try:
                self.page.wait_for_load_state("networkidle", timeout=self.NETWORKIDLE_TIMEOUT)
            except Exception:
                self.page.wait_for_timeout(self.ACTION_WAIT)
            logger.warning(f"Used force click for element {element_id}")
            return ActionResult(True, f"Clicked element {element_id} (force)")
        except Exception as e2:
            logger.debug(f"Force click failed ({str(e2)[:80]}), trying JS click")

        # --- Strategy 3: JS click ---
        try:
            handle = el.element_handle(timeout=2000)
            if handle:
                self.page.evaluate("el => el.click()", handle)
                self._ids_stamped = False
                self.page.wait_for_timeout(self.ACTION_WAIT)
                logger.warning(f"Used JS click for element {element_id}")
                return ActionResult(True, f"Clicked element {element_id} (JS fallback)")
        except Exception as e3:
            pass

        return ActionResult(False, f"Click failed on {element_id}",
                            "Tried normal, force, and JS click — all failed. "
                            "Element may be hidden or covered by an overlay.")

    def type_text(self, element_id: int, text: str, press_enter: bool = False) -> ActionResult:
        """Type text into an input field. Clears existing value first."""
        try:
            self._ensure_ids()
            el = self.page.locator(f'[data-agent-id="{element_id}"]')

            if el.count() == 0:
                return ActionResult(False, f"Element {element_id} not found")

            el.click(timeout=self.ACTION_TIMEOUT)
            el.fill("", timeout=self.ACTION_TIMEOUT)
            el.type(text, delay=30)  # 30ms — slightly slower, fewer autocomplete race conditions

            if press_enter:
                self.page.keyboard.press("Enter")
                try:
                    self.page.wait_for_load_state("networkidle", timeout=self.NETWORKIDLE_TIMEOUT)
                except Exception:
                    self.page.wait_for_timeout(800)
                self._ids_stamped = False

            return ActionResult(True, f"Typed '{text}' into {element_id}" + (" + Enter" if press_enter else ""))
        except Exception as e:
            return ActionResult(False, f"Type failed on {element_id}", str(e)[:200])

    def press_key(self, key: str) -> ActionResult:
        """
        Press a keyboard key.
        Especially useful for autocomplete/dropdown interactions:
        ArrowDown to navigate suggestions, Enter to confirm, Escape to close, Tab to move focus.
        """
        try:
            self.page.keyboard.press(key)
            self.page.wait_for_timeout(350)  # give UI time to react
            self._ids_stamped = False
            return ActionResult(True, f"Pressed {key}")
        except Exception as e:
            return ActionResult(False, f"Key press failed: {key}", str(e)[:200])

    def scroll_page(self, direction: str = "down") -> ActionResult:
        try:
            self.page.mouse.wheel(0, 500 if direction == "down" else -500)
            self.page.wait_for_timeout(200)
            self._ids_stamped = False
            return ActionResult(True, f"Scrolled {direction}")
        except Exception as e:
            return ActionResult(False, "Scroll failed", str(e)[:200])

    def scroll_to_element(self, element_id: int) -> ActionResult:
        """Scroll the element with the given ID into the center of the viewport."""
        try:
            self._ensure_ids()
            el = self.page.locator(f'[data-agent-id="{element_id}"]')
            if el.count() == 0:
                return ActionResult(False, f"Element {element_id} not found")
            el.scroll_into_view_if_needed(timeout=self.ACTION_TIMEOUT)
            self.page.wait_for_timeout(150)
            return ActionResult(True, f"Scrolled to element {element_id}")
        except Exception as e:
            return ActionResult(False, f"Scroll to element failed", str(e)[:200])

    def select_option(self, element_id: int, value: str) -> ActionResult:
        """
        Select an option from a <select> dropdown by visible label text.
        Use this for native HTML selects (date pickers, passenger count, ticket class, etc.).
        Falls back to selecting by value attribute if label match fails.
        """
        try:
            self._ensure_ids()
            el = self.page.locator(f'[data-agent-id="{element_id}"]')
            if el.count() == 0:
                return ActionResult(False, f"Element {element_id} not found")

            # Try by visible label first, then by value attribute
            try:
                el.select_option(label=value, timeout=self.ACTION_TIMEOUT)
            except Exception:
                el.select_option(value=value, timeout=self.ACTION_TIMEOUT)

            self._ids_stamped = False
            self.page.wait_for_timeout(self.ACTION_WAIT)
            return ActionResult(True, f"Selected '{value}' in element {element_id}")
        except Exception as e:
            return ActionResult(False, f"select_option failed on {element_id}", str(e)[:200])

    def find_text_on_page(self, query: str) -> ActionResult:
        """
        Search the full page text for a query string (case-insensitive).
        Returns up to 3 matching excerpts with ±200 chars of surrounding context.
        Useful when needed info is below the visible fold or outside the 1500-char page_text window.
        """
        try:
            full_text: str = _clean(self.page.evaluate("document.body ? document.body.innerText : ''"))
            if not full_text:
                return ActionResult(False, "Page has no text content")

            query_lower = query.lower()
            text_lower = full_text.lower()

            matches = []
            start = 0
            while len(matches) < 3:
                idx = text_lower.find(query_lower, start)
                if idx == -1:
                    break
                snippet_start = max(0, idx - 200)
                snippet_end = min(len(full_text), idx + len(query) + 200)
                snippet = full_text[snippet_start:snippet_end].strip()
                matches.append(f"...{snippet}...")
                start = idx + 1

            if not matches:
                return ActionResult(False, f"'{query}' not found on this page")

            result = f"Found {len(matches)} match(es) for '{query}':\n\n" + "\n\n---\n\n".join(matches)
            return ActionResult(True, result)
        except Exception as e:
            return ActionResult(False, "find_text_on_page failed", str(e)[:200])

    def wait(self, seconds: float = 2) -> ActionResult:
        seconds = min(max(seconds, 0.3), 10)
        self.page.wait_for_timeout(int(seconds * 1000))
        return ActionResult(True, f"Waited {seconds}s")

    # ------------------------------------------------------------------ #
    # Page State Extraction                                                #
    # ------------------------------------------------------------------ #

    def get_visual_state(self) -> PageState:
        """Get current page state with screenshot for vision model context."""
        return self._extract_state(with_screenshot=True)


    def extract_page_state(self) -> PageState:
        """Get page state without screenshot (cheaper)."""
        return self._extract_state(with_screenshot=False)

    def _extract_state(self, with_screenshot: bool = False) -> PageState:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self._wait_for_dom()

                if with_screenshot:
                    script = f"{self._js_script}\nwindow.drawAgentMarkers();"
                    elements = self.page.evaluate(script)
                    self._ids_stamped = True

                    os.makedirs("screenshots", exist_ok=True)
                    ts = self.page.evaluate("Date.now()")
                    screenshot_path = os.path.join(
                        os.path.dirname(__file__), f"screenshots/state_{ts}.png"
                    )
                    self.page.screenshot(path=screenshot_path)
                    self.page.evaluate("window.clearAgentMarkers()")

                    title = self.page.title()
                    url = self.page.url
                    # Use getViewportText (defined in _js_script) instead of raw body.innerText.
                    # getViewportText collects text from ancestors of interactive elements,
                    # giving us product names/prices/labels instead of nav menus and headers.
                    page_text = _clean(self.page.evaluate(
                        f"window.getViewportText ? window.getViewportText({config.MAX_PAGE_TEXT_CHARS}) : "
                        f"(document.body ? document.body.innerText : '').substring(0, {config.MAX_PAGE_TEXT_CHARS})"
                    ))

                    return PageState(
                        url=url,
                        title=title,
                        elements=elements,
                        page_text=page_text,
                        screenshot_path=screenshot_path,
                        has_captcha=self._detect_captcha(url, title, page_text),
                    )
                else:
                    script = f"{self._js_script}\nwindow.extractPageStateJSON();"
                    result_json = self.page.evaluate(script)
                    self._ids_stamped = True
                    data = json.loads(result_json)
                    url = data.get("url", self.page.url)
                    title = data.get("title", "")
                    page_text = _clean(data.get("page_text", ""))[:config.MAX_PAGE_TEXT_CHARS]
                    # Sanitize element text values too — they come from innerText
                    elements = data.get("elements", [])
                    for el in elements:
                        if "text" in el:
                            el["text"] = _clean(el["text"])
                    return PageState(
                        url=url,
                        title=title,
                        elements=elements,
                        page_text=page_text,
                        has_captcha=self._detect_captcha(url, title, page_text),
                    )

            except Exception as e:
                err = str(e)
                if "context was destroyed" in err or "Execution context" in err:
                    logger.warning(f"Context destroyed (attempt {attempt+1}/{max_retries}), waiting...")
                    try:
                        self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception:
                        pass
                    self.page.wait_for_timeout(400)
                    self._ids_stamped = False
                    continue
                logger.error(f"Page state extraction failed: {e}")
                try:
                    return PageState(url=self.page.url, title=self.page.title())
                except Exception:
                    return PageState(url="error", title="error")

        return PageState(url=self.page.url, title="extraction failed")

    # ------------------------------------------------------------------ #
    # Internals                                                            #
    # ------------------------------------------------------------------ #

    def _wait_for_dom(self):
        """Best-effort wait for DOM to be ready."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            self.page.wait_for_function("document.body !== null", timeout=3000)
        except Exception:
            pass

    def _ensure_ids(self):
        """
        Stamp data-agent-id attributes only if DOM has changed since last stamp.
        Avoids re-running the full JS extraction on every click/type.
        """
        if self._ids_stamped:
            # Quick sanity check — if IDs vanished (React re-render), re-stamp
            try:
                count = self.page.evaluate("document.querySelectorAll('[data-agent-id]').length")
                if count > 0:
                    return
            except Exception:
                pass

        try:
            self.page.evaluate(f"{self._js_script}\nwindow.extractPageStateJSON();")
            self._ids_stamped = True
        except Exception:
            pass

    def _detect_captcha(self, url: str, title: str, page_text: str) -> bool:
        """Detect if the current page is showing a captcha challenge."""
        combined = f"{url} {title} {page_text}".lower()
        return any(signal in combined for signal in _CAPTCHA_SIGNALS)

    def screenshot(self, path: str = "screenshot.png") -> str:
        full_path = os.path.join(os.path.dirname(__file__), path)
        self.page.screenshot(path=full_path)
        return full_path