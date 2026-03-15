"""
Vision Agent — ReAct loop: Observe → Think → Act → repeat.

Single-agent architecture. One LLM call per step, one browser action per step.

Multi-item tasks:
  - Pre-call before the main loop extracts the item list from the task.
  - Checklist is injected into every observation so it survives context trimming.
  - Agent marks items done/unavailable via mark_subtask_done.
"""

import base64
import datetime
import json
import logging
import os

from rich.console import Console
from rich.panel import Panel

from agents.base_agent import BaseAgent
from browser_controller import BrowserController
from dom_compressor import DOMCompressor
from tools.tool_registry import BROWSER_TOOLS
import config

logger = logging.getLogger(__name__)
console = Console()

KEEP_RECENT_STATES = 1
LOOP_DETECTION_WINDOW = 4
CONTEXT_SIZE_WARN = 150_000

STATUS_PLANNED = "planned"
STATUS_DONE = "done"
STATUS_UNAVAILABLE = "unavailable"


def _delete_screenshot(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# ------------------------------------------------------------------ #
# Step Logger                                                          #
# ------------------------------------------------------------------ #

class StepLogger:
    """Per-run markdown log. Written to logs/run_<timestamp>.md."""

    def __init__(self, task: str):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs("logs", exist_ok=True)
        self.path = f"logs/run_{ts}.md"
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(f"# Agent Run — {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
            f.write(f"**Task:** {task}\n\n---\n\n")
        console.print(f"[dim]Log: {self.path}[/dim]")

    def log_step(self, step: int, thought: str, tool_name: str, tool_args: dict,
                 result: str, checklist: str = "", target: str = "") -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"## Step {step}\n\n")
            if target:
                f.write(f"**Target:** `{target}`\n\n")
            if checklist:
                f.write(f"<details><summary>Checklist</summary>\n\n```\n{checklist}\n```\n\n</details>\n\n")
            if thought.strip():
                quoted = "\n> ".join(thought.strip().splitlines())
                f.write(f"**Thought:**\n> {quoted}\n\n")
            else:
                f.write("**Thought:** *(none)*\n\n")
            args_str = json.dumps(tool_args, ensure_ascii=False, indent=2)
            f.write(f"**Action:** `{tool_name}`\n\n```json\n{args_str}\n```\n\n")
            f.write(f"**Result:** `{result[:500]}`\n\n---\n\n")

    def log_finish(self, result: str, steps: int) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"## Done in {steps} steps\n\n**Result:** {result}\n")

    def log_timeout(self, max_steps: int) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"## Timed out after {max_steps} steps\n")


# ------------------------------------------------------------------ #
# Vision Agent                                                         #
# ------------------------------------------------------------------ #

class VisionAgent(BaseAgent):
    """Autonomous browser agent."""

    def __init__(self, browser: BrowserController):
        prompt_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "agent_system.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()

        super().__init__(system_prompt=system_prompt, tools=BROWSER_TOOLS)
        self.browser = browser

        self._action_history: list[str] = []
        self._nudge_count: int = 0
        self._checklist: list[dict] = []
        self._recent_actions: list[str] = []
        self._confirmed_done: list[str] = []
        self._last_search_term: str = ""
        self._empty_streak: int = 0
        self._step_logger: StepLogger | None = None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def run(self, task: str) -> str:
        self.reset()
        self._action_history = []
        self._nudge_count = 0
        self._checklist = []
        self._recent_actions = []
        self._confirmed_done = []
        self._last_search_term = ""
        self._empty_streak = 0
        self._step_logger = StepLogger(task)

        console.print(Panel(f"[bold cyan]{task}[/bold cyan]", title="Agent", border_style="cyan"))

        items = self._plan_task(task)
        if items:
            self._checklist = [{"item": i, "status": STATUS_PLANNED} for i in items]
            console.print(f"[dim]{len(items)} items: {', '.join(items)}[/dim]")

        self.add_user_message(
            f"Your goal: {task}\n\n"
            "I'll show you the browser state at each step. Use tools to complete the goal."
        )

        for step in range(1, config.MAX_STEPS + 1):
            console.print(f"\n[bold blue]── Step {step}/{config.MAX_STEPS} ──[/bold blue]")

            self._trim_old_observations()
            self._trim_old_tool_results()
            self._warn_context_size()

            page_state = self.browser.get_visual_state()
            want_screenshot = self._count_page_states() < KEEP_RECENT_STATES

            obs = self._build_observation(page_state, include_screenshot=want_screenshot)
            self.add_user_message(obs)

            if page_state.has_captcha:
                console.print("[bold yellow]Captcha detected[/bold yellow]")
                self.add_tool_result(
                    "captcha-detection",
                    "SYSTEM: Captcha detected. Call ask_user to have the user solve it manually.",
                )

            try:
                response = self.call_llm()
            except Exception as e:
                logger.exception("LLM call failed")
                return f"Error: LLM call failed — {e}"

            thought = response["content"]
            if thought:
                console.print(f"[cyan]{thought}[/cyan]")

            if not response["tool_calls"]:
                logger.warning("No tool_calls (streak=%d). content=%r",
                               self._empty_streak + 1, thought[:300])
                self._empty_streak += 1
                if self._empty_streak >= 3:
                    return "Error: model returned no tool calls 3 times in a row."
                self.add_user_message("You must call a tool. Pick ONE action from the page state above.")
                continue
            self._empty_streak = 0

            nudge = self._check_loop(response["tool_calls"])
            if nudge:
                self._nudge_count += 1
                logger.warning("Loop nudge #%d", self._nudge_count)
                console.print("[bold yellow]Loop detected[/bold yellow]")
                self._action_history = self._action_history[:-LOOP_DETECTION_WINDOW]
                self.add_user_message(nudge)
                continue

            self._nudge_count = 0
            done, result, free_steps = self._execute_tools(response["tool_calls"])

            if self._step_logger and response["tool_calls"]:
                tc = response["tool_calls"][0]
                self._step_logger.log_step(
                    step=step, thought=thought,
                    tool_name=tc["name"], tool_args=tc["arguments"],
                    result=result or "(pending)",
                    checklist=self._build_checklist_section(),
                    target=self._current_target,
                )

            if done:
                if self._step_logger:
                    self._step_logger.log_finish(result, step)
                return result

        msg = f"Task timed out after {config.MAX_STEPS} steps."
        if self._step_logger:
            self._step_logger.log_timeout(config.MAX_STEPS)
        console.print(Panel(f"[red]{msg}[/red]", title="", border_style="red"))
        return msg

    # ------------------------------------------------------------------ #
    # Pre-call planning                                                    #
    # ------------------------------------------------------------------ #

    def _plan_task(self, task: str) -> list[str]:
        """Extract ordered item list for multi-item tasks. Returns [] for single-step tasks."""
        prompt = (
            "Extract an ordered list of distinct items to collect or complete from this task.\n"
            "IMPORTANT: If the task implies a composite goal whose components are not explicitly listed "
            "(a recipe, a standard kit, a well-known set of items) — "
            "use your knowledge to infer and list all the necessary standard items for that goal.\n"
            "Return ONLY a valid JSON array of short item names in the language of the task, "
            'e.g. ["subtask1", "subtask2"]. '
            "Return [] ONLY if this is a single-step task with no items to collect.\n\n"
            f"Task: {task}"
        )
        try:
            resp = self._client_().chat.completions.create(
                model=config.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=200,
            )
            text = (resp.choices[0].message.content or "[]").strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            items = json.loads(text)
            if isinstance(items, list):
                return [str(i).strip() for i in items if str(i).strip()]
        except Exception as e:
            logger.warning("_plan_task failed (%s) — running without checklist", e)
        return []

    # ------------------------------------------------------------------ #
    # Checklist                                                            #
    # ------------------------------------------------------------------ #

    @property
    def _current_target(self) -> str:
        for e in self._checklist:
            if e["status"] == STATUS_PLANNED:
                return e["item"]
        return ""

    def _checklist_update(self, item: str, status: str) -> str:
        item = item.strip()
        for entry in self._checklist:
            if entry["item"].lower() == item.lower():
                entry["status"] = status
                break
        else:
            self._checklist.append({"item": item, "status": status})

        pending = [e["item"] for e in self._checklist if e["status"] == STATUS_PLANNED]
        next_item = pending[0] if pending else None

        if status == STATUS_DONE:
            if item not in self._confirmed_done:
                self._confirmed_done.append(item)
            msg = f"✓ '{item}' done."
            msg += f" Next: '{next_item}'." if next_item else " All items complete."
            return msg

        if status == STATUS_UNAVAILABLE:
            msg = f"✗ '{item}' unavailable."
            msg += f" Move on to: '{next_item}'." if next_item else " No more items."
            return msg

        return f"'{item}' → {status}"

    def _build_checklist_section(self) -> str:
        if not self._checklist:
            return ""

        lines = ["## Task Checklist"]
        found_current = False
        for entry in self._checklist:
            item, status = entry["item"], entry["status"]
            if status == STATUS_DONE:
                lines.append(f"  ✓ {item}")
            elif status == STATUS_UNAVAILABLE:
                lines.append(f"  ✗ {item} (unavailable)")
            elif not found_current:
                lines.append(f"  → {item}    ← NOW")
                found_current = True
            else:
                lines.append(f"  ○ {item}")

        done_names = [e["item"] for e in self._checklist if e["status"] == STATUS_DONE]
        if done_names:
            lines.append(f"⚠ Done — never search again: {', '.join(done_names)}")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Observation                                                          #
    # ------------------------------------------------------------------ #

    def _build_observation(self, page_state, include_screenshot: bool = True) -> list | str:
        compressed = DOMCompressor.compress(page_state, max_elements=90, max_text_chars=1300)

        sections = []

        if self._confirmed_done:
            items = "\n".join(f"  • {i}" for i in self._confirmed_done)
            sections.append(
                "## 🛒 Already done — DO NOT repeat\n"
                f"{items}\n"
                "Never search for or interact with any item above."
            )

        checklist = self._build_checklist_section()
        if checklist:
            sections.append(checklist)

        if self._recent_actions:
            sections.append(
                "## Recent actions\n" +
                "\n".join(f"  {e}" for e in self._recent_actions[-6:])
            )

        header = "\n\n".join(sections)
        text = (
            f"{header}\n\n## Current Page State\n{compressed}\n\nChoose your next action."
            if header
            else f"## Current Page State\n{compressed}\n\nChoose your next action."
        )

        if not page_state.screenshot_path:
            return text

        path = page_state.screenshot_path
        if not include_screenshot:
            _delete_screenshot(path)
            return text

        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            return [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]
        except Exception as e:
            logger.warning("Failed to read screenshot %s: %s", path, e)
            return text
        finally:
            _delete_screenshot(path)

    # ------------------------------------------------------------------ #
    # Tool execution                                                       #
    # ------------------------------------------------------------------ #

    def _execute_tools(self, tool_calls: list) -> tuple[bool, str, int]:
        """Returns (is_done, result, free_steps)."""
        for tc in tool_calls:
            name, args, tc_id = tc["name"], tc["arguments"], tc["id"]
            console.print(f"  [bold green]{name}[/bold green]({json.dumps(args, ensure_ascii=False)[:120]})")

            if name == "finish_task":
                result = args.get("result", "Task completed.")
                console.print(Panel(f"[bold green]{result}[/bold green]", title="Done", border_style="green"))
                return True, result, 0

            if name == "ask_user":
                question = args.get("question", "")
                console.print(Panel(f"[bold blue]{question}[/bold blue]", title="Question", border_style="blue"))
                answer = input("  Your answer: ").strip()
                self.add_tool_result(tc_id, f"User answered: {answer}")
                self._action_history = []
                self._nudge_count = 0
                return False, "", 1

            if name == "mark_subtask_done":
                result_str = self._checklist_update(args.get("subtask"), args.get("status", STATUS_DONE))
                console.print(f"    [dim]→ {result_str}[/dim]")
                self.add_tool_result(tc_id, result_str)
                # Reset spinning-wheels counter so next item gets a clean slate
                self._action_history = []
                return False, "", 0

            # Browser action
            self._record_action(tool_calls)
            result_str = self._run_browser_tool(name, args)
            console.print(f"    [dim]→ {result_str[:200]}[/dim]")
            self.add_tool_result(tc_id, result_str)
            self._update_recent_actions(name, args, result_str)
            break

        return False, "", 0

    def _run_browser_tool(self, name: str, args: dict) -> str:
        try:
            match name:
                case "click_element":    return str(self.browser.click_element(args["element_id"]))
                case "type_text":        return str(self.browser.type_text(args["element_id"], args["text"], args.get("press_enter", False)))
                case "select_option":    return str(self.browser.select_option(args["element_id"], args["value"]))
                case "press_key":        return str(self.browser.press_key(args["key"]))
                case "scroll_page":      return str(self.browser.scroll_page(args.get("direction", "down")))
                case "scroll_to_element":return str(self.browser.scroll_to_element(args["element_id"]))
                case "extract_hidden_text": return str(self.browser.find_text_on_page(args["query"]))
                case "go_to_url":        return str(self.browser.goto(args["url"]))
                case "go_back":          return str(self.browser.go_back())
                case "wait_for_page":    return str(self.browser.wait(args.get("seconds", 2)))
                case _:                  return f"Unknown tool: {name}"
        except Exception as e:
            return f"Tool error: {e}"

    def _update_recent_actions(self, tool_name: str, args: dict, result: str) -> None:
        if result.startswith("ERROR") or result.startswith("Tool error"):
            return
        if tool_name in ("scroll_page", "wait_for_page", "find_text_on_page", "go_back"):
            return

        target = self._current_target
        suffix = f" [target: {target}]" if target else ""

        match tool_name:
            case "go_to_url":
                entry = f"Navigated → {args.get('url', '')}"
            case "type_text":
                self._last_search_term = args.get("text", "")
                entry = f'Searched "{self._last_search_term}"{suffix}'
            case "click_element":
                ctx = f" (after: {self._last_search_term})" if self._last_search_term else ""
                entry = f"Clicked [{args.get('element_id')}]{ctx}{suffix} — {result[:80]}"
                self._last_search_term = ""
            case "select_option":
                entry = f"Selected '{args.get('value')}'{suffix}"
            case "press_key":
                entry = f"Pressed {args.get('key')}{suffix}"
            case _:
                entry = f"{tool_name}: {result[:80]}{suffix}"

        self._recent_actions.append(entry)
        self._recent_actions = self._recent_actions[-10:]

    # ------------------------------------------------------------------ #
    # Loop detection                                                       #
    # ------------------------------------------------------------------ #

    # Actions considered "searching/wandering" — no item was confirmed added
    _SEARCH_ONLY_ACTIONS = frozenset({"type_text", "scroll_page", "go_to_url", "go_back", "wait_for_page"})

    def _check_loop(self, tool_calls: list) -> str | None:
        if not tool_calls:
            return None

        tc = tool_calls[0]
        key = f"{tc['name']}:{json.dumps(tc['arguments'], sort_keys=True)}"
        window = self._action_history[-LOOP_DETECTION_WINDOW:]

        # Original: exact-same action N times in a row
        if len(window) >= LOOP_DETECTION_WINDOW and all(h == key for h in window):
            return (
                f"SYSTEM WARNING: You called {tc['name']} with the same arguments "
                f"{LOOP_DETECTION_WINDOW} times in a row. Try a completely different approach: "
                "different search term, different menu, go back, or ask the user."
            )

        # Original: too many scrolls
        if (tc["name"] == "scroll_page"
                and len(self._action_history) >= LOOP_DETECTION_WINDOW + 1
                and all(h.startswith("scroll_page:") for h in self._action_history[-(LOOP_DETECTION_WINDOW + 1):])):
            return (
                "SYSTEM WARNING: Too many scrolls without progress. "
                "Use a category menu, search bar, or different navigation."
            )

        # NEW: "spinning wheels" — last 5 actions are all search/navigation,
        # no productive click on an add/confirm button.
        SPIN_WINDOW = 5
        if len(self._action_history) >= SPIN_WINDOW:
            recent = self._action_history[-SPIN_WINDOW:]
            if all(h.split(":")[0] in self._SEARCH_ONLY_ACTIONS for h in recent):
                target = self._current_target
                target_hint = f" for '{target}'" if target else ""
                return (
                    f"SYSTEM WARNING: You have done {SPIN_WINDOW} search/navigation actions in a row"
                    f"{target_hint} without successfully adding anything. "
                    "You MUST do one of: (a) click an 'Add to cart' / confirm button you can already see, "
                    "(b) call mark_subtask_done with status='unavailable' and move on, "
                    "(c) ask_user if you are completely stuck."
                )

        return None

    def _record_action(self, tool_calls: list) -> None:
        if not tool_calls:
            return
        tc = tool_calls[0]
        key = f"{tc['name']}:{json.dumps(tc['arguments'], sort_keys=True)}"
        self._action_history.append(key)
        self._action_history = self._action_history[-50:]

    # ------------------------------------------------------------------ #
    # Context management                                                   #
    # ------------------------------------------------------------------ #

    def _count_page_states(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "user" and self._has_page_state(m))

    def _warn_context_size(self) -> None:
        total = sum(len(str(m)) for m in self.messages)
        if total > CONTEXT_SIZE_WARN:
            logger.warning("Context ~%d chars (%.0f%% of warn threshold)", total, total / CONTEXT_SIZE_WARN * 100)

    def _trim_old_tool_results(self) -> None:
        """Delete assistant+tool pairs beyond the rolling window."""
        pairs: list[tuple[int, int]] = []
        for i, m in enumerate(self.messages):
            if m.get("role") != "assistant" or not m.get("tool_calls"):
                continue
            tc_id = m["tool_calls"][0]["id"]
            for j in range(i + 1, len(self.messages)):
                if self.messages[j].get("role") == "tool" and self.messages[j].get("tool_call_id") == tc_id:
                    pairs.append((i, j))
                    break

        keep = KEEP_RECENT_STATES * 2
        for asst_idx, tool_idx in reversed(pairs[:-keep]):
            hi, lo = max(asst_idx, tool_idx), min(asst_idx, tool_idx)
            del self.messages[hi]
            del self.messages[lo]

    def _trim_old_observations(self) -> None:
        """Strip screenshots from old observations, then collapse to URL-only."""
        indices = [
            i for i, m in enumerate(self.messages)
            if m.get("role") == "user" and self._has_page_state(m)
        ]
        for idx in indices[:-KEEP_RECENT_STATES]:
            content = self.messages[idx]["content"]
            if isinstance(content, list):
                text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
                for block in text_blocks:
                    if "## Current Page State" in block.get("text", ""):
                        url_line = next((l for l in block["text"].splitlines() if l.startswith("URL:")), "")
                        block["text"] = f"[Page state trimmed] {url_line}"
                self.messages[idx]["content"] = text_blocks or "[Previous page state trimmed]"
            elif isinstance(content, str) and "## Current Page State" in content:
                url_line = next((l for l in content.splitlines() if l.startswith("URL:")), "")
                self.messages[idx]["content"] = f"[Page state trimmed] {url_line}"

    def _has_page_state(self, msg: dict) -> bool:
        content = msg.get("content", "")
        if isinstance(content, str):
            return "## Current Page State" in content
        if isinstance(content, list):
            return any("## Current Page State" in b.get("text", "") for b in content if isinstance(b, dict))
        return False