"""
DOM Compressor — Layer B
Transforms raw page state into a compact, token-efficient representation
that the AI agent can reason about.
"""

import logging
from browser_controller import PageState

logger = logging.getLogger(__name__)

# Element types that are almost never useful to the agent
_LOW_VALUE_TYPES = {"interactive"}  # bare catch-all type with no meaningful info


class DOMCompressor:
    """
    Compresses page state into a format optimized for LLM consumption.
    Converts elements list + page text into a readable, compact string.
    """

    @staticmethod
    def compress(
        state: PageState,
        max_elements: int = 80,
        max_text_chars: int = 1500,
    ) -> str:
        """
        Build a compact text representation of the current page for the LLM.

        Returns a string like:
            Page: Amazon.com — Shopping Cart
            URL: https://amazon.com/cart
            CAPTCHA DETECTED — call ask_user to request manual solving.

            Interactive elements (42 found):
            [1] link "Amazon" href=/
            [2] input placeholder="Search"
            [3] button "Add to Cart"
            ...

            Page content (visible text):
            Your cart has 2 items. Total: $134.99
        """
        lines = []

        # Page header
        lines.append(f"Page: {state.title}")
        lines.append(f"URL: {state.url}")

        if state.has_captcha:
            lines.append("CAPTCHA DETECTED — call ask_user to request the user solve it manually.")

        lines.append("")

        # Filter and cap elements
        elements = DOMCompressor._filter_elements(state.elements, max_elements)
        total = len(state.elements)
        shown = len(elements)

        if elements:
            suffix = f", showing first {shown}" if shown < total else ""
            lines.append(f"Interactive elements ({total} found{suffix}):")
            for el in elements:
                lines.append(DOMCompressor._format_element(el))
        else:
            lines.append("No interactive elements found on this page.")

        lines.append("")

        # Page text (trimmed + collapsed)
        if state.page_text:
            text = " ".join(state.page_text[:max_text_chars].split())
            lines.append("Page content (visible text):")
            lines.append(text)
        else:
            lines.append("(No visible text content)")

        return "\n".join(lines)

    @staticmethod
    def _filter_elements(elements: list, max_elements: int) -> list:
        """
        Prioritize useful elements before hard-capping at max_elements.
        Priority order:
          1. Buttons and links with meaningful text
          2. Inputs, selects, textareas
          3. Everything else
        Unnamed/typeless elements are dropped last.
        """
        def priority(el: dict) -> int:
            etype = el.get("type", "")
            text = el.get("text", "").strip().lower()
            # Priority 0: product action buttons (add to cart, buy, etc.)
            if etype == "button":
                return 0
            # Priority 1: search/auth inputs — agent needs these to navigate
            if etype in ("text", "search", "email", "password", "tel", "number",
                         "textarea", "select", "combobox"):
                return 1
            if etype == "input" and text:
                return 1
            # Priority 2: other named buttons (nav, filters, etc.)
            if etype == "button" and text:
                return 2
            # Priority 3: links
            if etype == "link" and text:
                return 3
            # Priority 4: anything else with text
            if text:
                return 4
            return 5

        sorted_els = sorted(elements, key=priority)
        return sorted_els[:max_elements]

    @staticmethod
    def _format_element(el: dict) -> str:
        """Format a single element into a compact readable line."""
        eid = el.get("id", "?")
        etype = el.get("type", "unknown")
        text = el.get("text", "")
        near = el.get("near", "")
        extra = el.get("extra", {})

        parts = [f"[{eid}]", etype]

        if text:
            parts.append(f'"{text}"')

        if extra:
            if "href" in extra:
                parts.append(f"href={extra['href']}")
            if "input_type" in extra:
                parts.append(f"type={extra['input_type']}")
            if "value" in extra:
                parts.append(f'value="{extra["value"]}"')
            if "checked" in extra:
                parts.append(f"checked={extra['checked']}")
            if extra.get("disabled"):
                parts.append("[disabled]")
            if "expanded" in extra:
                parts.append(f"expanded={extra['expanded']}")
            if "options" in extra:
                opts = ", ".join(extra["options"][:3])
                parts.append(f"options=[{opts}]")

        if near:
            parts.append(f'near "{near[:60].rstrip()}"')

        return " ".join(parts)