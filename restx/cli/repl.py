"""Interactive REPL for exploring a loaded API specification."""

from __future__ import annotations

import shutil
import sys
import threading
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

from restx.cli.colors import (
    MUTED_TEXT_STYLE,
    STATUS_LINE_FG,
    STATUS_LINE_SEPARATOR_STYLE,
    build_prompt_message,
    create_console,
    print_error,
    print_info,
    resolve_colors_enabled,
)
from restx.cli.commands import (
    command_should_exit,
    display_repl_help,
    format_curl_output,
    handle_curl_command,
    handle_curl_selection,
    handle_shell_inline,
    handle_shell_interactive,
    is_exit_command,
    parse_repl_command,
)
from restx.cli.completer import RestXCompleter
from restx.cli.tree_view import (
    parse_selection,
    selection_error,
)
from restx.cli.history import create_history
from restx.cli.viewer import (
    build_repl_bottom_toolbar,
    display_match_results,
    repl_interaction_mode,
    run_pager_session,
    run_repl_detail_view,
)
from restx.core import (
    ParsedSpec,
    QueryContext,
    QueryParseError,
    RestXCoreError,
    execute_query,
    load_spec,
)
from restx.core.spec_loader import SpinnerCallback, format_loaded_spec_message

SPINNER_FRAMES = "|/-\\"
SPINNER_MESSAGE = "Loading spec..."
SPINNER_INTERVAL = 0.08
REPL_PROMPT_PLACEHOLDER = HTML(
    "<placeholder>Type freeform text to search the API, globs and regexes also work. "
    ".ls to browse, .help for commands</placeholder>"
)


class PromptHintState:
    """Track conditional help-hint visibility for the REPL prompt."""

    def __init__(self) -> None:
        self.show = True
        self.typed = False

    def mark_idle(self) -> None:
        """Re-show the hint after returning from an idle trigger."""
        self.show = True
        self.typed = False

    def mark_keystroke(self) -> None:
        """Hide the hint as soon as the user types any character."""
        self.show = False
        self.typed = True

    def mark_submitted(self) -> None:
        """Hide the hint after the user submits a prompt line."""
        self.show = False

    def placeholder(self):
        """Return the placeholder text when the hint should be visible."""
        if self.show and not self.typed:
            return REPL_PROMPT_PLACEHOLDER
        return None


class LoadingSpinner:
    """In-place terminal spinner for spec load operations."""

    def __init__(self, stream=None, *, interval: float = SPINNER_INTERVAL) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._frame_index = 0
        self._active = False
        self._interval = interval
        self._message = SPINNER_MESSAGE
        self._stop_event = threading.Event()
        self._animation_thread: threading.Thread | None = None

    def _write_frame(self) -> None:
        frame = SPINNER_FRAMES[self._frame_index % len(SPINNER_FRAMES)]
        self._frame_index += 1
        self._stream.write(f"\r{self._message} {frame}")
        self._stream.flush()

    def _animate(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                self._write_frame()
            except OSError:
                break

    def start(self, message: str = SPINNER_MESSAGE) -> None:
        """Start or continue an animated spinner on one terminal line."""
        self._message = message
        self._active = True
        if self._animation_thread is not None and self._animation_thread.is_alive():
            return
        self._stop_event.clear()
        self._write_frame()
        self._animation_thread = threading.Thread(
            target=self._animate,
            daemon=True,
            name="restx-loading-spinner",
        )
        self._animation_thread.start()

    def tick(self, message: str = SPINNER_MESSAGE) -> None:
        self.start(message)

    def clear(self) -> None:
        self._stop_event.set()
        if self._animation_thread is not None:
            self._animation_thread.join(timeout=1.0)
            self._animation_thread = None
        if not self._active:
            return
        clear_width = len(self._message) + 3
        self._stream.write(f"\r{' ' * clear_width}\r")
        self._stream.flush()
        self._active = False


def make_spec_loader_spinner_callback(
    spinner: LoadingSpinner | None = None,
) -> SpinnerCallback:
    """Build a core-layer spinner callback that animates in-place on the terminal."""
    active_spinner = spinner or LoadingSpinner()

    def callback(phase: str) -> None:
        if phase == "done":
            active_spinner.clear()
            return
        active_spinner.start()

    return callback


def load_spec_with_spinner(source: str | None = None) -> ParsedSpec:
    """Load a spec while showing an in-place loading spinner when stdout is a TTY."""
    spinner = make_spec_loader_spinner_callback() if sys.stdout.isatty() else None
    return load_spec(source, spinner=spinner)


def normalize_spec_source(source: str | None) -> str:
    """Return a display label for the loaded spec source."""
    if source is None:
        return "stdin"
    return source


def _terminal_rows() -> int:
    try:
        return shutil.get_terminal_size(fallback=(80, 24)).lines
    except OSError:
        return 24


def clear_repl_screen(console) -> None:
    """Clear the terminal when running interactively."""
    if sys.stdout.isatty():
        console.clear()


def pad_prompt_to_bottom(*, lines_at_top: int = 0) -> None:
    """Insert blank lines so the next prompt sits above the status toolbar."""
    if not sys.stdout.isatty():
        return
    # Prompt row, separator row, and status row sit at the bottom.
    bottom_reserved_rows = 3
    padding = max(0, _terminal_rows() - lines_at_top - bottom_reserved_rows)
    if padding:
        sys.stdout.write("\n" * padding)
        sys.stdout.flush()


def format_repl_launch_summary(spec: ParsedSpec, api_source: str) -> str:
    """Return a compact one-line summary shown when the REPL starts."""
    _ = api_source
    return format_loaded_spec_message(spec)


def prepare_repl_launch(console, spec: ParsedSpec, api_source: str) -> None:
    """Print the loaded-spec summary before the first REPL prompt."""
    print_info(console, format_repl_launch_summary(spec, api_source))


def run_repl(
    spec: ParsedSpec,
    color_mode: str = "auto",
    *,
    spec_source: str | None = None,
    history_path=None,
    history_max: int | None = None,
) -> None:
    """Run the RestX REPL until the user exits."""
    color_enabled = resolve_colors_enabled(color_mode)
    console = create_console(color_enabled)
    context = QueryContext()
    completer = RestXCompleter(spec, context)
    history = create_history(history_path, history_max)
    api_source = normalize_spec_source(spec_source)

    def bottom_toolbar():
        try:
            columns = shutil.get_terminal_size(fallback=(80, 24)).columns
        except OSError:
            columns = 80
        return build_repl_bottom_toolbar(
            api_source,
            repl_interaction_mode(context_enabled=context.enabled),
            terminal_width=columns,
            color_enabled=color_enabled,
        )

    repl_style = None
    if color_enabled:
        repl_style = Style.from_dict(
            {
                "prompt": "bold ansiblue",
                "prompt-dim": "ansibrightblack",
                "placeholder": "ansibrightblack italic",
                "bottom-toolbar": "bg:default noreverse",
                "bottom-toolbar-separator": (
                    f"fg:{STATUS_LINE_SEPARATOR_STYLE} bg:default noreverse"
                ),
                "bottom-toolbar-status": f"fg:{STATUS_LINE_FG} bg:default noreverse",
            }
        )

    hint_state = PromptHintState()
    session = PromptSession(
        history=history,
        completer=completer,
        complete_while_typing=False,
        enable_history_search=True,
        bottom_toolbar=bottom_toolbar,
        style=repl_style,
    )
    pending_prompt_input = ""
    pending_curl_endpoints: tuple | None = None
    match_results = context.match_results

    prepare_repl_launch(console, spec, api_source)
    pad_prompt_to_bottom(lines_at_top=1)

    while True:
        prompt_message = build_prompt_message(context, color_enabled)
        try:
            raw_line = session.prompt(
                prompt_message,
                default=pending_prompt_input,
                placeholder=hint_state.placeholder(),
            )
            pending_prompt_input = ""
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if raw_line.strip():
            hint_state.mark_submitted()

        line = raw_line.strip()
        if not line:
            continue

        if pending_curl_endpoints is not None:
            selection_index = parse_selection(line)
            if selection_index is None:
                print_error(
                    console,
                    f"Expected a number between 1 and {len(pending_curl_endpoints)}.",
                )
                continue
            curl_result = handle_curl_selection(
                spec,
                pending_curl_endpoints,
                selection_index,
            )
            pending_curl_endpoints = None
            if curl_result.kind == "error":
                print_error(console, curl_result.message)
                continue
            console.print()
            console.print(format_curl_output(curl_result.message).rstrip())
            continue

        handled, should_exit, curl_pending, show_hint, reload = _handle_repl_command(
            raw_line,
            spec,
            context,
            console,
            color_enabled,
        )
        if reload is not None:
            spec, api_source = reload
            completer.spec = spec
            match_results.set_matches([])
        if show_hint:
            hint_state.mark_idle()
        if curl_pending is not None:
            pending_curl_endpoints = curl_pending
        if handled:
            if should_exit:
                break
            continue

        selection_index = parse_selection(line)
        if selection_index is not None:
            if not match_results.has_matches:
                print_error(
                    console,
                    selection_error(selection_index, match_results.match_count),
                )
                continue
            endpoint = match_results.select(selection_index)
            if endpoint is None:
                print_error(
                    console,
                    selection_error(selection_index, match_results.match_count),
                )
                continue
            run_repl_detail_view(selection_index, endpoint, spec, console)
            match_results.close_detail()
            continue

        try:
            if context.enabled:
                matches = context.execute(line, spec)
            else:
                matches = execute_query(line, spec)
        except QueryParseError as exc:
            print_error(console, str(exc))
            continue

        match_results.set_matches(matches)
        display_match_results(console, matches)


def _handle_repl_command(
    line: str,
    spec: ParsedSpec,
    context: QueryContext,
    console,
    color_enabled: bool,
) -> tuple[bool, bool, tuple | None, bool, tuple[ParsedSpec, str] | None]:
    """Handle non-query REPL commands.

    Returns ``(handled, should_exit, pending_curl_endpoints, show_prompt_hint, reload)``.
    """
    parsed = parse_repl_command(line)
    if parsed is None:
        return False, False, None, False, None

    if is_exit_command(parsed):
        return True, True, None, False, None

    if parsed.name == "help":
        display_repl_help(console, spec, color_enabled=color_enabled)
        return True, False, None, True, None

    if parsed.name == "status":
        for status_line in context.status_lines(spec):
            if color_enabled and status_line.startswith("Context filter:"):
                print_info(console, status_line.split(":", 1)[0] + ": ", end="")
                console.print(
                    status_line.split(":", 1)[1].strip(),
                    style=MUTED_TEXT_STYLE,
                )
            else:
                print_info(console, status_line)
        return True, False, None, False, None

    if parsed.name == "unknown":
        command_text = f".{parsed.args}" if parsed.args else "."
        print_error(console, f"Unknown command: {command_text}")
        return True, False, None, False, None

    if parsed.name == "context_reset":
        if not context.enabled:
            print_info(console, "Context mode is not enabled.")
        else:
            context.reset()
            print_info(console, "Context filter cleared.")
        return True, False, None, False, None

    if parsed.name == "clear":
        clear_repl_screen(console)
        return True, False, None, False, None

    if parsed.name == "context_on":
        context.enable()
        print_info(console, "Contextual query mode enabled.")
        return True, False, None, False, None

    if parsed.name == "context_off":
        context.disable()
        print_info(console, "Contextual query mode disabled.")
        return True, False, None, False, None

    if parsed.name == "shell_inline":
        handle_shell_inline(console, parsed.args)
        return True, False, None, False, None

    if parsed.name == "shell":
        handle_shell_interactive()
        return True, False, None, False, None

    if parsed.name == "curl":
        curl_result = handle_curl_command(spec, parsed.args)
        if curl_result.kind == "error":
            print_error(console, curl_result.message)
            return True, False, None, False, None
        if curl_result.kind == "select":
            console.print()
            console.print(curl_result.message)
            return True, False, curl_result.endpoints, False, None
        console.print()
        console.print(format_curl_output(curl_result.message).rstrip())
        return True, False, None, False, None

    if parsed.name == "ls":
        filter_pattern = parsed.args or None
        run_pager_session(spec, console, filter_pattern=filter_pattern)
        return True, False, None, True, None

    if parsed.name == "load":
        source = parsed.args.strip()
        if not source:
            print_error(console, "Usage: .load <file_or_url>")
            return True, False, None, False, None
        try:
            new_spec = load_spec_with_spinner(source)
        except RestXCoreError as exc:
            print_error(console, str(exc))
            return True, False, None, False, None

        clear_repl_screen(console)
        print_info(console, format_loaded_spec_message(new_spec))
        context.reset()
        context.match_results.set_matches([])
        pad_prompt_to_bottom(lines_at_top=1)
        return True, False, None, True, (new_spec, normalize_spec_source(source))

    return False, False, None, False, None
