"""CLI command handlers for the presentation layer."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Literal

from restx import __version__
from restx.cli.colors import create_console, print_error, resolve_colors_enabled
from restx.core import RestXCoreError
from restx.core.curl_generator import (
    format_endpoint_choices,
    generate_curl,
    resolve_endpoint_match,
)
from restx.core.models import Endpoint, ParsedSpec
from restx.core.shell import launch_interactive_shell, run_shell_command
from restx.core.spec_loader import format_loaded_spec_message

SHELL_OUTPUT_HEADER = "--- Shell Output ---"

META_COMMAND_ROOTS = frozenset(
    {
        "help",
        "?",
        "status",
        "clear",
        "quit",
        "ls",
        "curl",
        "shell",
        "context",
        "load",
    }
)

DOT_REPL_COMMANDS: tuple[str, ...] = (
    ".help",
    ".?",
    ".status",
    ".context on",
    ".context off",
    ".context reset",
    ".clear",
    ".quit",
    ".q",
    ".ls",
    ".curl",
    ".shell",
    ".load",
)


@dataclass(frozen=True)
class ParsedReplCommand:
    """A parsed REPL meta-command."""

    name: str
    args: str = ""


@dataclass(frozen=True)
class CurlCommandResult:
    """Outcome of resolving a /curl pattern."""

    kind: Literal["curl", "select", "error"]
    message: str = ""
    endpoints: tuple[Endpoint, ...] = ()


def handle_help(args) -> None:
    """Display CLI help per PRD section 13.1."""
    print("Usage: restx.py [SPEC_URL_OR_FILE] [OPTIONS]")
    print()
    print("Interactive command-line tool for exploring REST API specifications.")
    print()
    print("Input methods:")
    print("  URL     Argument starts with http:// or https://")
    print("  File    Any other non-empty argument")
    print("  Stdin   No argument provided and stdin is not a TTY")
    print()
    print("Arguments:")
    print("  SPEC_URL_OR_FILE    OpenAPI/Swagger spec URL, local file path, or stdin")
    print()
    print("Options:")
    print("  --color [always|never|auto]  Enable/disable colorization (default: auto)")
    print("  --version                    Show program version number")
    print("  --help, -h                   Show this help message and exit")
    print()
    print("Examples:")
    print("  restx.py https://petstore.swagger.io/v2/swagger.json")
    print("  restx.py ./local-spec.json")
    print("  cat spec.json | restx.py")
    print()
    print("GitHub Repo: https://github.com/cruxcoder/rest-explorer")


def handle_version(args) -> None:
    """Display version."""
    _ = args
    print(f"restx {__version__}")


def handle_run(args) -> None:
    """Main execution flow: load spec from URL, file, or stdin and start REPL."""
    from restx.cli.repl import load_spec_with_spinner, normalize_spec_source, run_repl

    color_mode = args.color

    if args.spec_source is None and sys.stdin.isatty():
        print("Welcome to RestX v1.0!")
        print("Provide a spec file or URL: restx <spec-url-or-file>")
        print("  restx ./local-spec.yaml")
        print("  restx https://petstore.swagger.io/v2/swagger.json")
        print("  cat spec.json | restx")
        return

    if sys.stdout.isatty():
        color_enabled = resolve_colors_enabled(color_mode, is_tty=True)
        create_console(color_enabled).clear()

    try:
        spec = load_spec_with_spinner(args.spec_source)
    except RestXCoreError as exc:
        color_enabled = resolve_colors_enabled(color_mode, is_tty=sys.stderr.isatty())
        console = create_console(color_enabled)
        print_error(console, str(exc))
        sys.exit(1)

    run_repl(
        spec,
        color_mode=color_mode,
        spec_source=normalize_spec_source(args.spec_source),
    )


def parse_repl_command(line: str) -> ParsedReplCommand | None:
    """Parse a REPL meta-command line into name and args.

    Meta-commands require a ``.`` prefix at the very first character (for example ``.help``),
    or a ``!`` prefix for inline shell commands (for example ``!ls``).
    """
    if not line:
        return None

    if line.startswith("!"):
        return ParsedReplCommand("shell_inline", line[1:].strip())

    if not line.startswith("."):
        return None

    remainder = line[1:].strip()
    return _parse_dot_command(remainder)


def _parse_dot_command(remainder: str) -> ParsedReplCommand | None:
    """Parse the body of a dot-prefixed meta-command."""
    if not remainder:
        return None

    lower_remainder = remainder.lower()
    if lower_remainder in {"help", "?"}:
        return ParsedReplCommand("help")
    if lower_remainder == "context on":
        return ParsedReplCommand("context_on")
    if lower_remainder == "context off":
        return ParsedReplCommand("context_off")
    if lower_remainder == "context reset":
        return ParsedReplCommand("context_reset")
    if lower_remainder in {"quit", "q", "exit"}:
        return ParsedReplCommand("quit")

    parts = remainder.split(None, 1)
    name = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    if name == "context" and args.lower() == "reset":
        return ParsedReplCommand("context_reset")
    if name in {"quit", "q", "exit"}:
        return ParsedReplCommand("quit")
    if name not in META_COMMAND_ROOTS:
        return None
    return ParsedReplCommand(name, args)


def build_repl_help_lines(spec: ParsedSpec) -> list[str]:
    """Build the static REPL help block lines."""
    from restx.cli.help import build_repl_help_lines as _build_repl_help_lines

    return _build_repl_help_lines(spec)


def display_repl_help(console, spec: ParsedSpec, *, color_enabled: bool = True) -> None:
    """Display the REPL help block and return to the prompt."""
    from restx.cli.help import display_repl_help as _display_repl_help

    _display_repl_help(console, spec, color_enabled=color_enabled)


def is_exit_command(parsed: ParsedReplCommand) -> bool:
    """Return True when the parsed command should exit the REPL."""
    return parsed.name == "quit"


def command_should_exit(line: str) -> bool:
    """Return True when a raw REPL line is an exit command."""
    parsed = parse_repl_command(line)
    return parsed is not None and is_exit_command(parsed)


def handle_shell_inline(console, command: str) -> None:
    """Execute an inline shell command and print output in the REPL."""
    if not command.strip():
        print_error(console, "Usage: ! <shell_command>")
        return

    output = run_shell_command(command)
    console.print()
    console.print(SHELL_OUTPUT_HEADER)
    if output:
        console.print(output)
    else:
        console.print("(no output)")


def handle_shell_interactive() -> None:
    """Drop into an interactive shell and return to the REPL afterward."""
    launch_interactive_shell()


def handle_curl_command(spec: ParsedSpec, pattern: str) -> CurlCommandResult:
    """Resolve a /curl pattern and return curl output or a selection prompt."""
    if not pattern.strip():
        return CurlCommandResult(
            kind="error",
            message="Usage: .curl <endpoint_pattern>",
        )

    result = resolve_endpoint_match(pattern, spec)
    if result.is_empty:
        return CurlCommandResult(
            kind="error",
            message=f"No endpoints match pattern: {pattern}",
        )

    if result.is_unique:
        return CurlCommandResult(
            kind="curl",
            message=generate_curl(result.endpoints[0], spec),
        )

    choices = format_endpoint_choices(result.endpoints)
    prompt = f"Select [1-{len(result.endpoints)}]:"
    return CurlCommandResult(
        kind="select",
        message="\n".join(choices) + f"\n{prompt}",
        endpoints=result.endpoints,
    )


def handle_curl_selection(
    spec: ParsedSpec,
    endpoints: tuple[Endpoint, ...],
    index: int,
) -> CurlCommandResult:
    """Generate curl output for a numbered endpoint selection."""
    if index < 1 or index > len(endpoints):
        return CurlCommandResult(
            kind="error",
            message=(
                f"Invalid selection: {index}. "
                f"Choose a number between 1 and {len(endpoints)}."
            ),
        )

    endpoint = endpoints[index - 1]
    return CurlCommandResult(
        kind="curl",
        message=generate_curl(endpoint, spec),
    )


def format_curl_output(curl_command: str) -> str:
    """Format curl command output as a plain copy-paste block."""
    return curl_command.rstrip() + "\n"
