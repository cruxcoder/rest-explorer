"""REPL help text formatting and display."""

from __future__ import annotations

from rich.text import Text

from restx.config import THEME
from restx.core.models import ParsedSpec
from restx.core.spec_loader import format_loaded_spec_message


def _meta_command_entries() -> list[tuple[str, str]]:
    """Return ``(command, description)`` pairs for meta-commands."""
    return [
        (".help, .?", "Show this help message"),
        (".status", "Show loaded spec and context information"),
        (".context on", "Enable contextual query mode"),
        (".context off", "Disable contextual query mode"),
        (".context reset", "Clear the accumulated context filter"),
        (".clear", "Clear the screen"),
        (".load <file_or_url>", "Load a different OpenAPI/Swagger spec"),
        (".ls [pattern]", "Full-screen API browser"),
        (".curl <pattern>", "Generate curl command for an endpoint"),
        (".shell", "Drop to interactive shell"),
        ("! <cmd>", "Execute shell command inline"),
        (".quit, .q", "Exit the REPL"),
    ]


def _format_command_column(entries: list[tuple[str, str]]) -> list[str]:
    """Align command and description columns with computed padding."""
    command_width = max(len(command) for command, _ in entries)
    gap = 2
    return [
        f"  {command.ljust(command_width)}{' ' * gap}{description}"
        for command, description in entries
    ]


def build_repl_help_lines(spec: ParsedSpec) -> list[str]:
    """Build the static REPL help block as plain text lines."""
    return [
        "RestX Help",
        "",
        format_loaded_spec_message(spec),
        "",
        "Meta-commands (use . prefix):",
        *_format_command_column(_meta_command_entries()),
        "",
        "Query DSL:",
        "  GET /users* req:email              Method, path glob, request field",
        "  (POST || PUT) && /users*           Boolean OR / AND with grouping",
        "  method != DELETE && resp:error*    Negation and response fields",
        "  reqpath:id                         Path parameter names only",
        "",
        "Field prefixes:",
        "  req:       Input parameters (query, body, headers)",
        "  resp:      Response schema field names",
        "  reqpath:   Path parameters (e.g. {id} in /users/{id})",
        "",
        "Selection:",
        "  <n>           Open drill-down view for match number n",
        "  select <n>    Open drill-down view for match number n",
        "",
        "Navigation:",
        "  Tab           Context-aware completion",
        "  Up/Down       Navigate command history",
        "  Ctrl+R        Reverse history search",
        "  Ctrl+D        Exit the REPL",
    ]


def _append_command_line(output: Text, command: str, description: str, *, command_width: int) -> None:
    gap = 2
    output.append(f"  {command.ljust(command_width)}", style=THEME["HELP_COMMAND"])
    output.append(" " * gap, style=THEME["HELP_DESC"])
    output.append(description, style=THEME["HELP_DESC"])
    output.append("\n")


def display_repl_help(console, spec: ParsedSpec, *, color_enabled: bool = True) -> None:
    """Display the REPL help block and return to the prompt."""
    if not color_enabled:
        for line in build_repl_help_lines(spec):
            console.print(line)
        console.print()
        return

    entries = _meta_command_entries()
    command_width = max(len(command) for command, _ in entries)

    console.print("RestX Help", style=THEME["HELP_HEADER"])
    console.print()
    console.print(format_loaded_spec_message(spec))
    console.print()
    console.print("Meta-commands (use . prefix):", style=THEME["HELP_HEADER"])

    for command, description in entries:
        line = Text()
        _append_command_line(
            line,
            command,
            description,
            command_width=command_width,
        )
        console.print(line, end="")

    for header, body_lines in (
        (
            "Query DSL:",
            [
                "  GET /users* req:email              Method, path glob, request field",
                "  (POST || PUT) && /users*           Boolean OR / AND with grouping",
                "  method != DELETE && resp:error*    Negation and response fields",
                "  reqpath:id                         Path parameter names only",
            ],
        ),
        (
            "Field prefixes:",
            [
                "  req:       Input parameters (query, body, headers)",
                "  resp:      Response schema field names",
                "  reqpath:   Path parameters (e.g. {id} in /users/{id})",
            ],
        ),
        (
            "Selection:",
            [
                "  <n>           Open drill-down view for match number n",
                "  select <n>    Open drill-down view for match number n",
            ],
        ),
        (
            "Navigation:",
            [
                "  Tab           Context-aware completion",
                "  Up/Down       Navigate command history",
                "  Ctrl+R        Reverse history search",
                "  Ctrl+D        Exit the REPL",
            ],
        ),
    ):
        console.print()
        console.print(header, style=THEME["HELP_HEADER"])
        for body_line in body_lines:
            console.print(body_line, style=THEME["HELP_DESC"])

    console.print()
