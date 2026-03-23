"""Rich CLI interface for Blog Manager."""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import click
from agents import Agent, RunContextWrapper, Runner
from agents.lifecycle import RunHooksBase
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from blog_manager.config import ASSETS_IMG_DIR, BLOG_DIR, POSTS_DIR
from blog_manager.pipeline.orchestrator import create_orchestrator_agent
from blog_manager.pipeline.trend_researcher import create_trend_researcher_agent

console = Console()

_BANNER = """\
[bold cyan] ██████╗ ██╗      ██████╗   ██████╗    ███████╗ ██████╗ ██████╗   ██████╗ ███████╗[/bold cyan]
[bold cyan] ██╔══██╗██║     ██╔═══██╗ ██╔════╝    ██╔════╝██╔═══██╗██╔══██╗ ██╔════╝ ██╔════╝[/bold cyan]
[bold cyan] ██████╔╝██║     ██║   ██║ ██║  ███╗   █████╗  ██║   ██║██████╔╝ ██║  ███╗█████╗  [/bold cyan]
[bold cyan] ██╔══██╗██║     ██║   ██║ ██║   ██║   ██╔══╝  ██║   ██║██╔══██╗ ██║   ██║██╔══╝  [/bold cyan]
[bold cyan] ██████╔╝███████╗╚██████╔╝ ╚██████╔╝   ██║     ╚██████╔╝██║  ██╗ ╚██████╔╝███████╗[/bold cyan]
[bold cyan] ╚═════╝ ╚══════╝ ╚═════╝   ╚═════╝    ╚═╝      ╚═════╝ ╚═╝  ╚═╝  ╚═════╝ ╚══════╝[/bold cyan]
[dim]BlogForge · OpenAI Agents SDK + OpenRouter + Tavily[/dim]"""

_AGENT_LABELS: dict[str, str] = {
    "Orchestrator": "🎯 Orchestrator",
    "Researcher": "🔍 Researcher",
    "TrendResearcher": "📈 Trend Researcher",
    "Writer": "✍️  Writer",
    "Critic": "🔎 Critic",
}
_TOOL_LABELS: dict[str, str] = {
    "tavily_search": "🌐 Searching web",
    "generate_illustration": "🎨 Generating illustration",
    "list_blog_posts": "📂 Listing existing posts",
    "read_blog_post": "📖 Reading existing post",
    "save_blog_post": "💾 Saving blog post",
    "research_topic": "🔍 Researching topic",
    "write_blog_post": "✍️  Writing draft",
    "critique_post": "🔎 Critiquing draft",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter(path: Path) -> dict[str, str]:
    """Parse Jekyll YAML frontmatter from a markdown file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text[3:].split("---", 1)
    if len(parts) < 2:
        return {}
    fm: dict[str, str] = {}
    current_list_key: str | None = None
    current_list_values: list[str] = []
    for raw_line in parts[0].strip().splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if current_list_key and stripped.startswith("- "):
            current_list_values.append(_unquote_frontmatter_value(stripped[2:].strip()))
            continue
        if current_list_key:
            fm[current_list_key] = ", ".join(current_list_values)
            current_list_key = None
            current_list_values = []
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        value = val.strip()
        if value:
            fm[key] = _unquote_frontmatter_value(value)
        else:
            current_list_key = key
    if current_list_key:
        fm[current_list_key] = ", ".join(current_list_values)
    return fm


def _unquote_frontmatter_value(value: str) -> str:
    """Strip matching YAML-style quotes from simple scalar values."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _strip_frontmatter(text: str) -> str:
    """Remove Jekyll frontmatter block and Liquid tags for terminal rendering."""
    if text.startswith("---"):
        parts = text[3:].split("---", 1)
        text = parts[1].strip() if len(parts) >= 2 else text
    # strip Jekyll Liquid tags like {%- include ... -%}
    text = re.sub(r"\{%-?\s*include[^%]*%-?\}", "", text)
    return text


def _all_posts() -> list[Path]:
    """Return all blog posts sorted newest first, excluding macOS resource forks."""
    return sorted(
        [p for p in POSTS_DIR.glob("*.md") if not p.name.startswith(".")],
        reverse=True,
    )


def _find_latest_post() -> Path | None:
    posts = _all_posts()
    return max(posts, key=lambda p: p.stat().st_mtime) if posts else None


def _snapshot_post_mtimes() -> dict[Path, int]:
    """Capture current post modification times for save verification."""
    if not POSTS_DIR.exists():
        return {}
    return {
        path: path.stat().st_mtime_ns
        for path in POSTS_DIR.glob("*.md")
        if not path.name.startswith(".")
    }


def _detect_saved_post(before: dict[Path, int]) -> Path | None:
    """Return the post that was newly created or modified by the latest save."""
    candidates: list[Path] = []
    for path in POSTS_DIR.glob("*.md"):
        if path.name.startswith("."):
            continue
        previous_mtime = before.get(path)
        current_mtime = path.stat().st_mtime_ns
        if previous_mtime is None or current_mtime > previous_mtime:
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _post_slug(post: Path) -> str:
    """Extract slug from a post filename (strip YYYY-MM-DD- prefix)."""
    stem = post.stem
    m = re.match(r"^\d{4}-\d{2}-\d{2}-(.+)$", stem)
    return m.group(1) if m else stem


def _show_post(post_path: Path) -> None:
    """Render a single post to the terminal with Rich."""
    content = post_path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(post_path)
    body = _strip_frontmatter(content)

    header = Text()
    header.append(fm.get("title", post_path.stem), style="bold white")
    header.append(f"\n{fm.get('date', '')}  ·  tags: {fm.get('tags', '')}", style="dim")
    if fm.get("description"):
        header.append(f"\n{fm['description']}", style="italic")

    console.print()
    console.print(Panel(header, border_style="cyan"))
    console.print(Markdown(body))


def _git_deploy(post_path: Path, title: str) -> bool:
    """Git add → commit → push a post and its assets."""
    slug = _post_slug(post_path)
    assets_path = ASSETS_IMG_DIR / slug

    to_add = [str(post_path.relative_to(BLOG_DIR))]
    if assets_path.exists() and any(assets_path.iterdir()):
        to_add.append(str(assets_path.relative_to(BLOG_DIR)))

    try:
        subprocess.run(
            ["git", "-C", str(BLOG_DIR), "add", *to_add],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(BLOG_DIR), "commit", "-m", f"blog: add '{title}'"],
            check=True, capture_output=True,
        )
        result = subprocess.run(
            ["git", "-C", str(BLOG_DIR), "push"],
            check=True, capture_output=True, text=True,
        )
        console.print(f"[dim]{result.stdout or result.stderr}[/dim]")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        console.print(f"[red]Git error:[/red] {stderr or e}")
        return False


# ---------------------------------------------------------------------------
# ProgressHooks
# ---------------------------------------------------------------------------

def _tool_detail(tool_name: str, result: str) -> str:
    """Return a one-line human-readable summary of a tool result."""
    if not result:
        return ""
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return ""

    if tool_name == "research_topic" and isinstance(data, dict):
        topic = (data.get("topic") or "")[:40]
        n_findings = len(data.get("key_findings") or [])
        n_sources = len(data.get("sources") or [])
        parts = []
        if topic:
            parts.append(f'"{topic}"')
        if n_findings:
            parts.append(f"{n_findings} finding{'s' if n_findings != 1 else ''}")
        if n_sources:
            parts.append(f"{n_sources} source{'s' if n_sources != 1 else ''}")
        return " · ".join(parts)

    if tool_name == "write_blog_post" and isinstance(data, dict):
        title = (data.get("title") or "")[:50]
        word_count = len((data.get("content") or "").split())
        parts = []
        if title:
            parts.append(f'"{title}"')
        if word_count:
            parts.append(f"~{word_count} words")
        return " · ".join(parts)

    if tool_name == "critique_post" and isinstance(data, dict):
        score = data.get("overall_score")
        approved = data.get("approved")
        status = (
            "[green]✓ approved[/green]"
            if approved
            else "[red]✗ revision needed[/red]"
        )
        return f"score: {score}/10 · {status}" if score is not None else status

    if tool_name == "tavily_search" and isinstance(data, dict):
        items = data.get("results", [])
        count = len(items)
        q = (data.get("query") or "")[:45]
        first = (items[0].get("title") or "")[:50] if items else ""
        suffix = f" · {first}" if first else ""
        return f'{count} result{"s" if count != 1 else ""} for "{q}"{suffix}'

    if tool_name == "generate_illustration" and isinstance(data, dict):
        if data.get("error"):
            return f'[red]error:[/red] {str(data["error"])[:60]}'
        url = data.get("url") or ""
        return f"saved {Path(url).name}" if url else ""

    if tool_name == "save_blog_post" and isinstance(data, dict):
        if not data.get("success"):
            err = str(data.get("error", "unknown error"))[:80]
            return f"[red]FAILED:[/red] {err}"
        fname = data.get("filename") or ""
        n = data.get("images_downloaded", 0)
        img_str = f", {n} image{'s' if n != 1 else ''}" if n else ""
        return f"{fname}{img_str}" if fname else ""

    if tool_name == "list_blog_posts" and isinstance(data, list):
        return f'{len(data)} post{"s" if len(data) != 1 else ""} found'

    return ""


def _tool_input_detail(tool_name: str, args_str: str) -> str:
    """Return a short human-readable summary of what a tool is about to do."""
    try:
        args = json.loads(args_str)
    except (json.JSONDecodeError, TypeError):
        return ""

    if tool_name == "tavily_search":
        q = (args.get("query") or "")[:55]
        return f'"{q}"' if q else ""

    if tool_name == "read_blog_post":
        return (args.get("filename") or "")[:50]

    # Agent-as-tool: input is a plain-string message — show the first line as topic hint
    if tool_name == "research_topic":
        inp = str(args.get("input") or "")
        return inp.splitlines()[0][:60] if inp else ""

    return ""


class ProgressHooks(RunHooksBase):
    """RunHooks that show a rich live display of agent/tool activity."""

    def __init__(self, live: Live) -> None:
        self._live = live
        self._log: list[str] = []
        self._status_agent = ""   # name of agent currently running a tool
        self._status_tool = ""    # label of tool currently in flight
        self._start = time.monotonic()
        # Queue of pending tool-call argument strings, keyed by tool name
        self._pending_inputs: dict[str, deque[str]] = defaultdict(deque)

    # -- rendering ------------------------------------------------------------

    def _elapsed(self) -> str:
        s = int(time.monotonic() - self._start)
        return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"

    def _render(self) -> Panel:
        grid = Table.grid(padding=(0, 1))

        # Live status row: shown only while a tool is running
        if self._status_tool:
            agent_lbl = _AGENT_LABELS.get(self._status_agent, self._status_agent)
            grid.add_row(
                f"[bold]{agent_lbl}[/bold]  "
                f"[yellow]{self._status_tool}[/yellow] [dim]…[/dim]"
            )
            grid.add_row("[dim]" + "─" * 62 + "[/dim]")

        for line in self._log[-30:]:
            grid.add_row(line)

        return Panel(
            grid,
            title=(
                f"[bold cyan]🤖 Agents at work[/bold cyan]"
                f"  [dim]{self._elapsed()}[/dim]"
            ),
            border_style="cyan",
        )

    def _push(self, line: str) -> None:
        self._log.append(line)
        self._live.update(self._render())

    def _refresh(self) -> None:
        self._live.update(self._render())

    # -- hooks ----------------------------------------------------------------

    async def on_agent_start(
        self, context: RunContextWrapper, agent: Agent
    ) -> None:
        self._status_agent = agent.name
        label = _AGENT_LABELS.get(agent.name, f"🤖 {agent.name}")
        self._push(f"[bold]{label}[/bold]")

    async def on_agent_end(
        self, context: RunContextWrapper, agent: Agent, output: Any
    ) -> None:
        label = _AGENT_LABELS.get(agent.name, f"🤖 {agent.name}")
        self._push(f"  [green]✓[/green] {label} [dim]done[/dim]")

    async def on_handoff(
        self,
        context: RunContextWrapper,
        from_agent: Agent,
        to_agent: Agent,
    ) -> None:
        from_lbl = _AGENT_LABELS.get(from_agent.name, from_agent.name)
        to_lbl = _AGENT_LABELS.get(to_agent.name, to_agent.name)
        self._push(f"  [dim]⟶[/dim] {from_lbl} [dim]→[/dim] {to_lbl}")

    async def on_llm_end(
        self, context: RunContextWrapper, agent: Agent, response: Any
    ) -> None:
        """Capture upcoming tool-call arguments so on_tool_start can display them."""
        for item in getattr(response, "output", []):
            if getattr(item, "type", None) == "function_call":
                self._pending_inputs[item.name].append(item.arguments)

    async def on_tool_start(
        self, context: RunContextWrapper, agent: Agent, tool: Any
    ) -> None:
        self._status_agent = agent.name
        name = getattr(tool, "name", "")
        base_label = _TOOL_LABELS.get(name, f"🔧 {name}")
        pending = self._pending_inputs.get(name)
        input_detail = _tool_input_detail(name, pending[0]) if pending else ""
        self._status_tool = (
            f"{base_label}  [dim]{input_detail}[/dim]" if input_detail else base_label
        )
        self._refresh()

    async def on_tool_end(
        self, context: RunContextWrapper, agent: Agent, tool: Any, result: str
    ) -> None:
        name = getattr(tool, "name", "")
        pending = self._pending_inputs.get(name)
        if pending:
            pending.popleft()
        label = _TOOL_LABELS.get(name, f"🔧 {name}")
        detail = _tool_detail(name, result)
        self._push(
            f"    [green]✓[/green] {label}"
            + (f"  [dim]↳ {detail}[/dim]" if detail else "")
        )
        # For the critic: surface top feedback items when revision is needed
        if name == "critique_post":
            try:
                data = json.loads(result)
                if not data.get("approved", True):
                    for fb in (data.get("feedback") or [])[:2]:
                        self._push(f"      [dim]• {str(fb)[:80]}[/dim]")
            except (json.JSONDecodeError, TypeError):
                pass
        self._status_tool = ""
        self._refresh()


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

async def _run_pipeline(
    topic: str, instructions: str | None, dry_run: bool
) -> tuple[str, Path | None]:
    """Run the multi-agent pipeline and verify that non-dry runs produced a saved post."""
    prompt_parts = [f"Topic: {topic}"]
    if instructions:
        prompt_parts.append(f"User instructions: {instructions}")
    if dry_run:
        prompt_parts.append(
            "Note: This is a dry run. Research and write the post but DO NOT call save_blog_post."
        )
    user_prompt = "\n\n".join(prompt_parts)

    orchestrator = create_orchestrator_agent()
    posts_before = _snapshot_post_mtimes()

    with Live(
        Panel(Text("Initializing agents…", style="dim"),
              title="[bold cyan]🤖 Agents at work[/bold cyan]  [dim]0s[/dim]",
              border_style="cyan"),
        console=console,
        refresh_per_second=8,
    ) as live:
        hooks = ProgressHooks(live)
        result = await Runner.run(orchestrator, user_prompt, max_turns=30, hooks=hooks)

    final_output = str(result.final_output) if result.final_output else ""
    if dry_run:
        return final_output, None

    saved_post = _detect_saved_post(posts_before)
    if saved_post is None:
        raise RuntimeError(
            "The pipeline finished without creating or updating a blog post. "
            "No saved file was detected."
        )
    return final_output, saved_post


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """📝 Blog Manager — AI-powered multi-agent blog post creator."""
    console.print()
    console.print(Panel(_BANNER, border_style="cyan", padding=(0, 1)))
    console.print()
    if ctx.invoked_subcommand is None:
        _interactive_menu_loop(ctx)


_EXIT_MENU = "6"  # key that maps to "Exit"

_MENU_ITEMS = [
    ("1", "✍️  Write a new post", "write"),
    ("2", "📈 Trend research", "trends"),
    ("3", "📚 List all posts", "list"),
    ("4", "📖 View a post", "view"),
    ("5", "🚀 Deploy a post", "deploy"),
    (_EXIT_MENU, "🚪 Exit", None),
]


def _print_menu() -> None:
    table = Table.grid(padding=(0, 2))
    for key, label, _ in _MENU_ITEMS:
        table.add_row(f"  [bold cyan]{key}[/bold cyan]", label)
    console.print(Panel(table,
                        title="[bold yellow]What would you like to do?[/bold yellow]",
                        border_style="yellow"))
    console.print()


def _confirm_exit() -> None:
    """Show exit-confirmation prompt. Ctrl+C again (or 'y') exits; Enter cancels."""
    console.print(
        "\n[yellow]⚠  Press Ctrl+C again to exit, "
        "or press Enter to return to the menu.[/yellow]"
    )
    try:
        Prompt.ask("", default="")
        # User pressed Enter → stay in the app
    except KeyboardInterrupt:
        console.print("\n[bold]👋  Goodbye![/bold]\n")
        sys.exit(0)


def _interactive_menu_loop(ctx: click.Context) -> None:
    """Main interactive loop — runs until the user exits via menu or Ctrl+C×2."""
    while True:
        _print_menu()
        try:
            choice = Prompt.ask(
                "[bold yellow]Select[/bold yellow]",
                choices=[k for k, _, _ in _MENU_ITEMS],
                default="1",
            )
        except KeyboardInterrupt:
            _confirm_exit()
            continue

        cmd_name = next(cmd for k, _, cmd in _MENU_ITEMS if k == choice)
        if cmd_name is None:
            # "Exit" selected from menu (option 5)
            console.print("\n[bold]👋  Goodbye![/bold]\n")
            sys.exit(0)

        console.print()
        try:
            ctx.invoke(main.commands[cmd_name])  # type: ignore[attr-defined]
        except KeyboardInterrupt:
            console.print("\n[yellow]Task interrupted.[/yellow]")
        except SystemExit:
            pass  # subcommands may raise SystemExit; swallow so loop continues

        console.print()
        console.print(Rule("[dim]Back to menu[/dim]", style="dim"))
        console.print()


@main.command()
@click.option("--topic", "-t", default=None, help="Blog topic to write about")
@click.option("--instructions", "-i", default=None,
              help="Extra instructions (tone, format, style)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Generate without saving to blog")
@click.option("--deploy", "-d", is_flag=True, default=False,
              help="Auto git-commit and push after saving (skips interactive menu)")
@click.option("--no-preview", is_flag=True, default=False,
              help="Skip the preview before the revise/publish menu")
def write(
    topic: str | None,
    instructions: str | None,
    dry_run: bool,
    deploy: bool,
    no_preview: bool,
) -> None:
    """Research a topic and write a new blog post."""
    if not topic:
        topic = Prompt.ask("[bold yellow]📌 Enter your blog topic[/bold yellow]")

    if instructions is None:
        console.print("[dim]Optional: tone, format, style — e.g. 'casual, listicle format'[/dim]")
        raw = Prompt.ask(
            "[bold yellow]📝 Extra instructions[/bold yellow] [dim](Enter to skip)[/dim]",
            default="",
        )
        instructions = raw.strip() or None

    task_text = Text()
    task_text.append("Topic: ", style="bold yellow")
    task_text.append(topic, style="white")
    if instructions:
        task_text.append("\nInstructions: ", style="bold yellow")
        task_text.append(instructions, style="dim white")
    if dry_run:
        task_text.append("\n[yellow]⚠  DRY RUN — post will not be saved[/yellow]")
    console.print(Panel(task_text, title="[bold]📋 Task", border_style="yellow"))
    console.print()

    try:
        final_output, saved_post = asyncio.run(_run_pipeline(topic, instructions, dry_run))
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted.[/red]")
        return
    except RuntimeError as exc:
        console.print()
        console.print(Rule("[bold red]❌ Save Failed", style="red"))
        console.print(f"[red]{exc}[/red]")
        return

    console.print()
    console.print(Rule("[bold green]✅ Pipeline Complete", style="green"))
    if final_output:
        console.print(Panel(final_output, title="[bold green]📄 Result", border_style="green"))

    if dry_run:
        return

    if saved_post is None:
        return

    _post_write_flow(saved_post, auto_deploy=deploy, show_preview=not no_preview)


# ---------------------------------------------------------------------------
# Trend research
# ---------------------------------------------------------------------------

_TREND_CATEGORIES = ["AI", "Consumer tech", "Engineering", "Game dev"]


async def _run_trend_research(category: str) -> dict:
    """Run the trend researcher agent and return parsed results."""
    agent = create_trend_researcher_agent()
    with Live(
        Panel(Text("Researching trends…", style="dim"),
              title="[bold cyan]📈 Trend Research[/bold cyan]  [dim]0s[/dim]",
              border_style="cyan"),
        console=console,
        refresh_per_second=8,
    ) as live:
        hooks = ProgressHooks(live)
        result = await Runner.run(agent, category, max_turns=15, hooks=hooks)

    raw = str(result.final_output) if result.final_output else ""
    # Strip markdown code fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    return json.loads(cleaned)


def _display_trends(data: dict) -> None:
    """Render trend research results to the console."""
    category = data.get("category", "")
    trends = data.get("trends", [])
    top_pick = data.get("top_pick", {})

    console.print()
    console.print(Rule(f"[bold cyan]📈 Trending in: {category}", style="cyan"))
    console.print()

    for i, trend in enumerate(trends, 1):
        title = trend.get("trend", "")
        desc = trend.get("description", "")
        suggested = trend.get("suggested_topic", "")
        why = trend.get("why_write_about_it", "")
        sources = trend.get("sources", [])

        panel_content = Text()
        panel_content.append(desc, style="white")
        panel_content.append("\n\nSuggested post: ", style="bold yellow")
        panel_content.append(suggested, style="italic white")
        if why:
            panel_content.append(f"\n{why}", style="dim")
        if sources:
            panel_content.append("\n\nSources: ", style="dim")
            panel_content.append(", ".join(sources[:3]), style="dim cyan")

        console.print(Panel(
            panel_content,
            title=f"[bold cyan]{i}.[/bold cyan] [bold]{title}[/bold]",
            border_style="cyan",
            padding=(0, 1),
        ))

    if top_pick:
        console.print()
        top_text = Text()
        top_text.append(top_pick.get("topic", ""), style="bold white")
        top_text.append(f"\n{top_pick.get('reason', '')}", style="dim")
        console.print(Panel(
            top_text,
            title="[bold green]⭐ Top Pick[/bold green]",
            border_style="green",
        ))


@main.command()
def trends() -> None:
    """Research trending topics in a category and suggest blog post ideas."""
    console.print("[bold yellow]Pick a category to research:[/bold yellow]\n")
    for i, cat in enumerate(_TREND_CATEGORIES, 1):
        console.print(f"  [bold cyan]{i}[/bold cyan]  {cat}")
    console.print(f"  [bold cyan]{len(_TREND_CATEGORIES) + 1}[/bold cyan]  Other (type your own)")
    console.print()

    choice = Prompt.ask(
        "[bold yellow]Select[/bold yellow]",
        choices=[str(i) for i in range(1, len(_TREND_CATEGORIES) + 2)],
        default="1",
    )

    idx = int(choice) - 1
    if idx < len(_TREND_CATEGORIES):
        category = _TREND_CATEGORIES[idx]
    else:
        category = Prompt.ask("[bold yellow]Enter your category[/bold yellow]").strip()
        if not category:
            console.print("[yellow]No category entered.[/yellow]")
            return

    console.print()
    task_text = Text()
    task_text.append("Category: ", style="bold yellow")
    task_text.append(category, style="white")
    console.print(Panel(task_text, title="[bold]📈 Trend Research", border_style="yellow"))
    console.print()

    try:
        data = asyncio.run(_run_trend_research(category))
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted.[/red]")
        return
    except (json.JSONDecodeError, Exception) as exc:
        console.print(f"\n[red]Error parsing trend results:[/red] {exc}")
        return

    _display_trends(data)

    # Offer to write a post based on a trend
    trends_list = data.get("trends", [])
    if not trends_list:
        return

    console.print()
    write_choice = Prompt.ask(
        "[bold yellow]Write a post? Pick a trend number or press Enter to skip[/bold yellow]",
        default="",
    )
    if not write_choice:
        return

    try:
        trend_idx = int(write_choice) - 1
        if trend_idx < 0 or trend_idx >= len(trends_list):
            raise ValueError
    except ValueError:
        console.print("[yellow]Invalid selection.[/yellow]")
        return

    selected = trends_list[trend_idx]
    topic = selected.get("suggested_topic", selected.get("trend", ""))
    console.print(f"\n[bold]Starting post on:[/bold] {topic}\n")

    console.print("[dim]Optional: tone, format, style — e.g. 'casual, listicle format'[/dim]")
    raw = Prompt.ask(
        "[bold yellow]📝 Extra instructions[/bold yellow] [dim](Enter to skip)[/dim]",
        default="",
    )
    instructions = raw.strip() or None

    try:
        final_output, saved_post = asyncio.run(_run_pipeline(topic, instructions, dry_run=False))
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted.[/red]")
        return
    except RuntimeError as exc:
        console.print()
        console.print(Rule("[bold red]❌ Save Failed", style="red"))
        console.print(f"[red]{exc}[/red]")
        return

    console.print()
    console.print(Rule("[bold green]✅ Pipeline Complete", style="green"))
    if final_output:
        console.print(Panel(final_output, title="[bold green]📄 Result", border_style="green"))

    if saved_post:
        _post_write_flow(saved_post, auto_deploy=False, show_preview=True)


# ---------------------------------------------------------------------------
# Post-write interactive loop: preview → revise / publish / done
# ---------------------------------------------------------------------------

async def _run_revision_pipeline(post_filename: str, revision_prompt: str) -> tuple[str, Path]:
    """Re-run the pipeline to revise an existing post and verify it was saved."""
    prompt = (
        f"REVISION TASK: Revise the existing blog post '{post_filename}'.\n\n"
        "Steps:\n"
        f"1. Call read_blog_post with filename='{post_filename}' to load the current content.\n"
        "2. Apply the revision instructions below. Keep all existing images and section "
        "structure unless told otherwise. "
        "Do NOT conduct new web research unless specifically required.\n"
        "3. Call save_blog_post to overwrite the post (same title/date/tags).\n\n"
        f"Revision instructions from user:\n{revision_prompt}"
    )
    orchestrator = create_orchestrator_agent()
    posts_before = _snapshot_post_mtimes()
    with Live(
        Panel(Text("Revising post…", style="dim"),
              title="[bold cyan]🤖 Agents at work[/bold cyan]  [dim]0s[/dim]",
              border_style="cyan"),
        console=console,
        refresh_per_second=8,
    ) as live:
        hooks = ProgressHooks(live)
        result = await Runner.run(orchestrator, prompt, max_turns=20, hooks=hooks)
    saved_post = _detect_saved_post(posts_before)
    if saved_post is None:
        raise RuntimeError(
            "The revision pipeline finished without updating the blog post. "
            "No saved file change was detected."
        )
    return (str(result.final_output) if result.final_output else "", saved_post)


def _post_write_flow(post_path: Path, *, auto_deploy: bool, show_preview: bool) -> None:
    """Interactive loop after a post is written: preview → revise / publish / done."""
    fm = _parse_frontmatter(post_path)
    title = fm.get("title", post_path.stem)

    first_iter = True
    while True:
        # Show preview (always after a revision; respect show_preview on first pass)
        if show_preview or not first_iter:
            console.print()
            console.print(Rule("[bold cyan]📖 Post Preview", style="cyan"))
            _show_post(post_path)
            console.print()
        first_iter = False

        # --deploy flag: skip menu and publish immediately
        if auto_deploy:
            _do_deploy(post_path, title)
            return

        # Action menu
        console.print(Panel(
            "  [bold cyan]1[/bold cyan]  ✍️  Revise with AI\n"
            "  [bold cyan]2[/bold cyan]  🚀 Publish (git commit + push)\n"
            "  [bold cyan]3[/bold cyan]  ✓  Done (save locally, publish later)",
            title="[bold yellow]What would you like to do?[/bold yellow]",
            border_style="yellow",
        ))
        choice = Prompt.ask(
            "[bold yellow]Select[/bold yellow]",
            choices=["1", "2", "3"],
            default="3",
        )

        if choice == "1":
            revision = Prompt.ask(
                "[bold yellow]📝 Describe your revisions[/bold yellow]"
            ).strip()
            if not revision:
                console.print("[yellow]No revision entered — try again.[/yellow]")
                continue
            try:
                _, post_path = asyncio.run(_run_revision_pipeline(post_path.name, revision))
            except KeyboardInterrupt:
                console.print("\n[red]Revision cancelled.[/red]")
                continue
            except RuntimeError as exc:
                console.print(f"\n[red]{exc}[/red]")
                continue
            console.print()
            console.print(Rule("[bold green]✅ Revision Complete", style="green"))
            fm = _parse_frontmatter(post_path)
            title = fm.get("title", post_path.stem)

        elif choice == "2":
            _do_deploy(post_path, title)
            return

        else:
            console.print("[dim]Post saved. Deploy later with:[/dim]")
            console.print(f"  [cyan]blog-manager deploy {post_path.name}[/cyan]")
            return


def _do_deploy(post_path: Path, title: str) -> None:
    with console.status("[bold cyan]Deploying…[/bold cyan]"):
        success = _git_deploy(post_path, title)
    if success:
        console.print(f"[bold green]✅ Deployed:[/bold green] {post_path.name}")
    else:
        console.print("[red]Deploy failed. Run manually:[/red]")
        cmd = f"cd {BLOG_DIR} && git add . && git commit -m 'blog: ...' && git push"
        console.print(f"  [dim]{cmd}[/dim]")


@main.command("list")
def list_posts() -> None:
    """List all existing blog posts."""
    posts = _all_posts()
    if not posts:
        console.print("[yellow]No posts found in _posts/[/yellow]")
        return

    table = Table(title="📚 Blog Posts", header_style="bold cyan", show_lines=True)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Date", width=12)
    table.add_column("Title")
    table.add_column("Tags", style="dim cyan")
    table.add_column("Description", style="dim")

    for i, post in enumerate(posts, 1):
        fm = _parse_frontmatter(post)
        desc = fm.get("description", "")
        table.add_row(
            str(i),
            fm.get("date", post.stem[:10]),
            fm.get("title", post.stem),
            fm.get("tags", ""),
            (desc[:60] + "…") if len(desc) > 60 else desc,
        )

    console.print()
    console.print(table)
    console.print(f"\n[dim]{len(posts)} post(s) in {POSTS_DIR}[/dim]")


@main.command()
@click.argument("filename", required=False)
def view(filename: str | None) -> None:
    """View a blog post. Pass a filename (or partial name), or pick from a list."""
    posts = _all_posts()
    if not posts:
        console.print("[yellow]No posts found.[/yellow]")
        return

    if filename:
        matches = [p for p in posts if filename in p.name]
        if not matches:
            console.print(f"[red]No post matching '{filename}'[/red]")
            return
        post_path = matches[0]
    else:
        for i, p in enumerate(posts, 1):
            fm = _parse_frontmatter(p)
            console.print(
                f"  [cyan]{i:2}.[/cyan]  {fm.get('date', p.stem[:10])}  "
                f"[bold]{fm.get('title', p.stem)}[/bold]"
            )
        raw = Prompt.ask("\nSelect post number", default="1")
        try:
            post_path = posts[int(raw) - 1]
        except (ValueError, IndexError):
            console.print("[red]Invalid selection.[/red]")
            return

    _show_post(post_path)


@main.command()
@click.argument("filename", required=False)
def deploy(filename: str | None) -> None:
    """Git commit and push a post. Uses the latest post if no filename given."""
    posts = _all_posts()
    if not posts:
        console.print("[yellow]No posts found.[/yellow]")
        return

    if filename:
        matches = [p for p in posts if filename in p.name]
        if not matches:
            console.print(f"[red]No post matching '{filename}'[/red]")
            return
        post_path = matches[0]
    else:
        post_path = _find_latest_post()
        if not post_path:
            return

    fm = _parse_frontmatter(post_path)
    title = fm.get("title", post_path.stem)

    console.print(f"\n[bold]Post:[/bold] {post_path.name}")
    console.print(f"[bold]Title:[/bold] {title}")

    if not Prompt.ask(
        "\n[bold yellow]🚀 Deploy (git commit + push)?[/bold yellow]",
        choices=["y", "n"], default="n",
    ) == "y":
        console.print("[dim]Cancelled.[/dim]")
        return

    _do_deploy(post_path, title)
