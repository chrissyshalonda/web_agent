"""
AI Browser Agent — Entry Point

1. Launch browser with persistent session (keeps login between runs).
2. Enter a task in plain language.
3. VisionAgent executes: Observe → Think → Act → repeat.
"""

import logging
import sys

import config
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel

from agents.base_agent import _sanitize
from browser_controller import BrowserController
from agents.vision_agent import VisionAgent

logging.basicConfig(
    level=logging.WARNING,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True)],
)
console = Console()


def main():
    if not config.API_KEY:
        console.print("[red]ERROR: OPENROUTER_API_KEY not set in .env[/red]")
        sys.exit(1)

    console.print(Panel(
        "[bold cyan]🤖 AI Browser Agent[/bold cyan]",
        subtitle=config.LLM_MODEL,
        border_style="cyan",
    ))

    browser = BrowserController()
    try:
        console.print("[cyan]Launching browser...[/cyan]")
        browser.launch()
        console.print("[bold green]✓ Browser ready[/bold green]\n")

        agent = VisionAgent(browser)
        browser.goto("https://www.google.com")

        while True:
            console.print()
            try:
                task = _sanitize(input("🤖 Task> ").strip())
            except (EOFError, KeyboardInterrupt):
                console.print("\n[cyan]Goodbye![/cyan]")
                break

            if not task:
                continue
            if task.lower() in ("quit", "exit", "q"):
                console.print("[cyan]Goodbye![/cyan]")
                break

            try:
                result = agent.run(task)
                console.print()
                console.print(Panel(f"[bold]{result}[/bold]", title="📊 Result", border_style="green"))
            except KeyboardInterrupt:
                console.print("\n[yellow]Task interrupted.[/yellow]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                logging.getLogger(__name__).exception("Task failed")
    finally:
        console.print("[cyan]Closing browser...[/cyan]")
        browser.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nShutdown.")