"""Terminal color detection and styled output for the RestX presentation layer."""

from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.text import Text

from restx.cli.match_list import (
    format_match_list_lines,
    format_match_list_rich_text,
    match_list_header_width,
)
from restx.config import THEME
from restx.core.context import QueryContext
from restx.core.matcher import (
    ZERO_MATCH_MESSAGE,
    format_params,
    match_header_segment,
    match_method_width,
)
from restx.core.models import Endpoint

# Light theme: default terminal background; status bar uses an accent fill.
APP_BACKGROUND = "grey70"
APP_BACKGROUND_HEX = "#F0F0F0"

# Base text: terminal default foreground (readable on light and dark backgrounds).
TEXT_FG = "default"
TEXT_FG_HEX = "#1a1a1a"
BASE_TEXT_STYLE = ""
MUTED_TEXT_STYLE = THEME["TEXT_MUTED"]
PARAM_NAME_STYLE = THEME["PARAM_NAME"]
MATCH_HEADER_STYLE = ""

# Status line: dark navy text on a transparent (theme-matched) background.
STATUS_LINE_BG_HEX = ""
STATUS_LINE_STYLE = THEME["STATUS_TEXT"]
STATUS_LINE_FG = THEME["STATUS_TEXT"]
STATUS_LINE_BG = ""
STATUS_LINE_ANSI = "\x1b[38;2;0;51;102m"
STATUS_LINE_ANSI_RESET = "\x1b[0m"

# Pager: selected row uses a subtle highlight; footer stays muted.
PAGER_SELECTED_BG_HEX = THEME["PAGER_SELECTED_BG"]
PAGER_SELECTED_STYLE = f"black on {PAGER_SELECTED_BG_HEX}"
PAGER_FOOTER_STYLE = THEME["PAGER_FOOTER"]

# Semantic HTTP method colors tuned for readability on a light background.
METHOD_STYLES = {
    "GET": THEME["METHOD_GET"],
    "POST": THEME["METHOD_POST"],
    "PUT": THEME["METHOD_PUT"],
    "DELETE": THEME["METHOD_DELETE"],
    "PATCH": THEME["METHOD_PATCH"],
}
PATH_STYLE = THEME["PATH"]

# Split-view / pager search match highlight: light lavender background, dark text.
SEARCH_HIGHLIGHT_BG_HEX = THEME["SEARCH_HIGHLIGHT_BG"]
SEARCH_HIGHLIGHT_STYLE = (
    f"{THEME['SEARCH_HIGHLIGHT_FG']} on {SEARCH_HIGHLIGHT_BG_HEX}"
)


def resolve_colors_enabled(
    color_mode: str,
    *,
    no_color_env: str | None = None,
    is_tty: bool | None = None,
) -> bool:
    """Resolve whether color output is enabled per PRD section 9.1."""
    if color_mode == "always":
        return True
    if color_mode == "never":
        return False

    if no_color_env is None:
        no_color_env = os.environ.get("NO_COLOR")
    if no_color_env:
        return False

    if is_tty is None:
        is_tty = sys.stdout.isatty()
    return bool(is_tty)


def create_console(color_enabled: bool) -> Console:
    """Create a Rich console configured for RestX light-theme output."""
    return Console(
        force_terminal=color_enabled,
        no_color=not color_enabled,
        highlight=False,
    )


def print_error(console: Console, message: str) -> None:
    """Print an error message in red."""
    console.print(message, style="red")


def print_warning(console: Console, message: str) -> None:
    """Print a warning message in yellow."""
    console.print(message, style="yellow")


def print_info(console: Console, message: str, **kwargs) -> None:
    """Print an informational message using the terminal default color."""
    console.print(message, **kwargs)


def wrap_status_line_ansi(text: str) -> str:
    """Wrap plain status text with light-theme ANSI styling."""
    return f"{STATUS_LINE_ANSI}{text}{STATUS_LINE_ANSI_RESET}"


STATUS_LINE_SEPARATOR_CHAR = "─"
# Extremely faint separator; foreground only, no background fill.
STATUS_LINE_SEPARATOR_STYLE = THEME["STATUS_SEPARATOR"]


def format_repl_status_separator(*, terminal_width: int = 80) -> str:
    """Return a thin horizontal rule spanning the terminal width."""
    return STATUS_LINE_SEPARATOR_CHAR * max(terminal_width, 1)


def build_repl_status_toolbar(
    status_line: str,
    *,
    terminal_width: int | None = None,
):
    """Build a prompt_toolkit bottom toolbar with light-theme status styling."""
    from prompt_toolkit.formatted_text import HTML
    from xml.sax.saxutils import escape

    width = terminal_width if terminal_width is not None else 80
    separator = format_repl_status_separator(terminal_width=width)
    return HTML(
        f'<bottom-toolbar-separator fg="{STATUS_LINE_SEPARATOR_STYLE}">'
        f"{escape(separator)}</bottom-toolbar-separator>\n"
        f'<bottom-toolbar-status fg="{STATUS_LINE_FG}">'
        f"{escape(status_line)}</bottom-toolbar-status>"
    )


def format_match_results_text(
    matches: list[Endpoint],
    *,
    terminal_width: int | None = None,
) -> Text | str:
    """Format match results with the light-theme color scheme."""
    if not matches:
        return Text(ZERO_MATCH_MESSAGE, style="yellow")

    count = len(matches)
    label = "match" if count == 1 else "matches"
    output = Text()
    output.append(f"  {count} {label}:\n", style=MATCH_HEADER_STYLE)

    if terminal_width is not None and terminal_width > 0:
        output.append_text(
            format_match_list_rich_text(
                matches,
                terminal_width=terminal_width,
            )
        )
        return output

    method_width = match_method_width(matches)
    header_width = match_list_header_width(matches)

    for index, endpoint in enumerate(matches, start=1):
        params = format_params(endpoint)
        _append_aligned_match_line(
            output,
            index,
            endpoint,
            params,
            header_width=header_width,
            method_width=method_width,
        )

    return output


def _append_aligned_match_line(
    output: Text,
    index: int,
    endpoint: Endpoint,
    params: str,
    *,
    header_width: int,
    method_width: int,
) -> None:
    header = match_header_segment(index, endpoint, method_width=method_width)
    padding = header_width - len(header)

    output.append("  [")
    output.append(str(index))
    output.append("] ")
    output.append(
        endpoint.method,
        style=METHOD_STYLES.get(endpoint.method, BASE_TEXT_STYLE),
    )
    output.append(" " * (method_width - len(endpoint.method) + 1))
    output.append(endpoint.path, style=PATH_STYLE)
    if padding > 0:
        output.append(" " * padding)

    if params:
        output.append(" params: ")
        for part_index, part in enumerate(_split_param_parts(params)):
            if part_index:
                output.append(", ")
            name, suffix = _split_param_name(part)
            output.append(name, style=PARAM_NAME_STYLE)
            if suffix:
                output.append(suffix, style=BASE_TEXT_STYLE)
    output.append("\n")


def build_prompt_message(context: QueryContext, color_enabled: bool):
    """Build a prompt_toolkit-compatible prompt with optional context styling."""
    from prompt_toolkit.formatted_text import HTML

    prompt = context.prompt_suffix()
    if not color_enabled:
        return prompt

    if prompt == "restx> ":
        return HTML("<prompt>restx&gt; </prompt>")

    if prompt == "restx (context)> ":
        return HTML(
            "<prompt>restx </prompt>"
            "<prompt-dim>(context)</prompt-dim>"
            "<prompt>&gt; </prompt>"
        )

    prefix = "restx (context)> ["
    if prompt.startswith(prefix) and prompt.endswith("] "):
        filter_text = prompt[len(prefix) : -2]
        return HTML(
            "<prompt>restx </prompt>"
            "<prompt-dim>(context)</prompt-dim>"
            "<prompt>&gt; </prompt>"
            f"<prompt-dim>[{filter_text}]</prompt-dim> "
        )

    return prompt


def _split_param_parts(params: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in params:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current).strip())
    return parts


def _split_param_name(part: str) -> tuple[str, str]:
    marker = " (path)"
    if part.endswith(marker):
        return part[: -len(marker)], marker
    return part, ""
