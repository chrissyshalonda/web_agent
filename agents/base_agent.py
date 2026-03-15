"""
Base Agent — ReAct loop over OpenRouter (OpenAI-compatible API).

One LLM call per step, one tool call per step.
"""

import json
import logging
import re
import time
from typing import Optional

from openai import OpenAI

import config

logger = logging.getLogger(__name__)

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _sanitize(obj):
    """Replace lone UTF-16 surrogates with U+FFFD (Playwright returns them on broken pages)."""
    if isinstance(obj, str):
        return _SURROGATE_RE.sub("\ufffd", obj)
    if isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    return obj


class BaseAgent:
    """
    LLM interaction via OpenRouter.

    Canonical message format:
        user:      {"role": "user",      "content": str | list}
        assistant: {"role": "assistant", "content": str,
                    "tool_calls": [{"id": str, "name": str, "arguments": dict}]}
        tool:      {"role": "tool",      "tool_call_id": str, "content": str}
    """

    def __init__(self, system_prompt: str, tools: list):
        self.system_prompt = system_prompt
        self.tools = tools
        self.messages: list[dict] = []
        self._client: Optional[OpenAI] = None

    def _client_(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=config.API_KEY,
                base_url=config.API_BASE_URL,
                timeout=config.LLM_TIMEOUT_S,
            )
        return self._client

    def reset(self):
        self.messages = []

    # ------------------------------------------------------------------ #
    # LLM call                                                             #
    # ------------------------------------------------------------------ #

    def call_llm(self) -> dict:
        """Call the LLM. Returns {"content": str, "tool_calls": list}."""
        kwargs = {
            "model": config.LLM_MODEL,
            "messages": _sanitize(self._build_api_messages()),
            "temperature": 0.1,
        }
        if self.tools:
            kwargs["tools"] = self.tools
            # required
            kwargs["tool_choice"] = "auto"
            kwargs["parallel_tool_calls"] = False

        t0 = time.perf_counter()
        response = self._client_().chat.completions.create(**kwargs)
        logger.info("LLM %.2fs [%s]", time.perf_counter() - t0, config.LLM_MODEL)

        message = response.choices[0].message
        tool_calls = self._parse_tool_calls(message.tool_calls or [])

        self.messages.append({
            "role": "assistant",
            "content": message.content or "",
            **({"tool_calls": tool_calls} if tool_calls else {}),
        })
        return {"content": message.content or "", "tool_calls": tool_calls}

    def _parse_tool_calls(self, raw: list) -> list:
        valid = {t["function"]["name"] for t in self.tools} if self.tools else set()
        result = []
        for tc in raw:
            name = tc.function.name
            # Some models append garbage tokens like "<|channel|>..."
            if "<|" in name:
                name = name.split("<|")[0]
                logger.warning("Sanitized tool name: %r -> %r", tc.function.name, name)
            if valid and name not in valid:
                logger.warning("Skipping unknown tool: %r", name)
                continue
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                logger.warning("Bad tool arguments: %s", tc.function.arguments)
                args = {}
            result.append({"id": tc.id, "name": name, "arguments": args})
        return result[:1]  # one action per step

    # ------------------------------------------------------------------ #
    # Message helpers                                                      #
    # ------------------------------------------------------------------ #

    def add_user_message(self, content):
        self.messages.append({"role": "user", "content": content})

    def add_tool_result(self, tool_call_id: str, result: str, context: Optional[str] = None):
        if context:
            result = f"{result}\n\n[Context]\n{context}"
        self._close_orphaned_tool_calls(tool_call_id)
        self.messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": result})

    def _close_orphaned_tool_calls(self, executed_id: str) -> None:
        """OpenAI requires a result for every tool_call_id in the last assistant turn."""
        last = self.messages[-1] if self.messages else {}
        if last.get("role") != "assistant":
            return
        answered = {m["tool_call_id"] for m in self.messages if m.get("role") == "tool"}
        for tc in last.get("tool_calls", []):
            if tc["id"] != executed_id and tc["id"] not in answered:
                logger.warning("Closing orphaned tool_call_id=%s", tc["id"])
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": "[skipped — one action per step]",
                })

    def _build_api_messages(self) -> list:
        """Convert canonical self.messages → OpenAI wire format."""
        result = [{"role": "system", "content": self.system_prompt}]
        for msg in self.messages:
            role = msg["role"]
            if role == "user":
                result.append({"role": "user", "content": msg["content"]})
            elif role == "assistant":
                m: dict = {"role": "assistant", "content": msg.get("content") or ""}
                if msg.get("tool_calls"):
                    m["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                            },
                        }
                        for tc in msg["tool_calls"]
                    ]
                result.append(m)
            elif role == "tool":
                result.append({
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "content": msg["content"],
                })
        return result