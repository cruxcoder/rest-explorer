"""API tree rendering, detail views, and full-screen pager for the RestX REPL."""

from __future__ import annotations

import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from io import StringIO

from rich.console import Console
from rich.tree import Tree

from restx.cli.colors import (
    BASE_TEXT_STYLE,
    MATCH_HEADER_STYLE,
    METHOD_STYLES,
    PARAM_NAME_STYLE,
    PAGER_FOOTER_STYLE,
    PAGER_SELECTED_STYLE,
    PATH_STYLE,
    SEARCH_HIGHLIGHT_STYLE,
    format_match_results_text,
)
from restx.cli.tree_view import (
    KEY_DOWN,
    KEY_UP,
    _read_key,
    build_endpoint_tree,
    is_escape_key,
    render_detail_tree_to_text,
    run_interactive_tree,
    tree_line_prefix_len,
    truncate_endpoint_summary,
)
from restx.core.curl_generator import generate_curl
from restx.core.models import Endpoint, ParsedSpec
from restx.core.pager import Pager
from restx.core.viewer import (
    ApiTreeNode,
    build_api_tree,
    endpoint_entry_id,
    endpoint_label,
    path_entry_id,
)
def render_endpoint_markup(
    endpoint: Endpoint,
    *,
    highlighted: bool = False,
    active: bool = False,
    max_line_width: int | None = None,
    tree_prefix_len: int = 0,
) -> str:
    """Render a single endpoint line with method color markup."""
    method_style = METHOD_STYLES.get(endpoint.method, BASE_TEXT_STYLE)
    summary = endpoint.summary or ""
    if summary:
        summary = truncate_endpoint_summary(
            endpoint.method,
            endpoint.path,
            summary,
            max_line_width=max_line_width,
            tree_prefix_len=tree_prefix_len,
        )
    if summary:
        text = (
            f"[{method_style}]{endpoint.method}[/{method_style}] "
            f"[{PATH_STYLE}]{endpoint.path}[/{PATH_STYLE}] — {summary}"
        )
    else:
        text = (
            f"[{method_style}]{endpoint.method}[/{method_style}] "
            f"[{PATH_STYLE}]{endpoint.path}[/{PATH_STYLE}]"
        )

    if active:
        return f"[{PAGER_SELECTED_STYLE}]{text}[/{PAGER_SELECTED_STYLE}]"
    if highlighted:
        return f"[{SEARCH_HIGHLIGHT_STYLE}]{text}[/{SEARCH_HIGHLIGHT_STYLE}]"
    return text


def _render_path_label(
    node: ApiTreeNode,
    *,
    highlighted: bool = False,
    active: bool = False,
) -> str:
    if node.children:
        prefix = "▼ " if node.expanded else "▶ "
    else:
        prefix = ""
    label = f"{prefix}{node.path}"
    if active:
        return (
            f"[{PAGER_SELECTED_STYLE}][bold {PATH_STYLE}]{label}[/bold {PATH_STYLE}]"
            f"[/{PAGER_SELECTED_STYLE}]"
        )
    if highlighted:
        return (
            f"[{SEARCH_HIGHLIGHT_STYLE}][bold {PATH_STYLE}]{label}[/bold {PATH_STYLE}]"
            f"[/{SEARCH_HIGHLIGHT_STYLE}]"
        )
    return f"[bold {PATH_STYLE}]{label}[/bold {PATH_STYLE}]"


def render_path_node(
    node: ApiTreeNode,
    *,
    active_entry_id: str | None = None,
    match_entry_ids: set[str] | None = None,
    content_width: int | None = None,
    depth: int = 1,
) -> Tree:
    """Render a path node and its visible descendants."""
    entry_id = path_entry_id(node.path)
    branch = Tree(
        _render_path_label(
            node,
            highlighted=entry_id in (match_entry_ids or set()),
            active=entry_id == active_entry_id,
        ),
        guide_style="grey50",
    )
    for endpoint in node.endpoints:
        endpoint_id = endpoint_entry_id(endpoint)
        endpoint_branch = Tree(
            render_endpoint_markup(
                endpoint,
                highlighted=endpoint_id in (match_entry_ids or set()),
                active=endpoint_id == active_entry_id,
                max_line_width=content_width,
                tree_prefix_len=tree_line_prefix_len(depth + 1),
            ),
            guide_style="grey50",
        )
        branch.add(endpoint_branch)
    if node.expanded:
        for child in node.children:
            branch.add(
                render_path_node(
                    child,
                    active_entry_id=active_entry_id,
                    match_entry_ids=match_entry_ids,
                    content_width=content_width,
                    depth=depth + 1,
                )
            )
    return branch


def render_api_tree(
    roots: list[ApiTreeNode],
    *,
    active_entry_id: str | None = None,
    match_entry_ids: set[str] | None = None,
    content_width: int | None = None,
) -> Tree:
    """Render the API tree honoring expanded nodes and search highlights."""
    tree = Tree("[bold blue]API Endpoints[/bold blue]")
    for node in roots:
        tree.add(
            render_path_node(
                node,
                active_entry_id=active_entry_id,
                match_entry_ids=match_entry_ids,
                content_width=content_width,
            )
        )
    return tree


def collect_rendered_root_labels(roots: list[ApiTreeNode]) -> list[str]:
    """Return endpoint labels rendered at the root level (for testing)."""
    labels: list[str] = []
    for node in roots:
        labels.append(node.path)
        labels.extend(endpoint_label(endpoint) for endpoint in node.endpoints)
    return labels


def render_tree_to_text(tree: Tree, console: Console, width: int) -> str:
    """Render a Rich tree to plain text (preserving markup in buffer)."""
    buffer = StringIO()
    panel_console = Console(
        file=buffer,
        width=max(width, 1),
        force_terminal=not console.no_color,
        no_color=console.no_color,
        highlight=False,
    )
    panel_console.print(tree)
    return buffer.getvalue()


TREE_PREFIX_CHARACTERS = frozenset("├│└─")


def build_endpoint_curl_command(endpoint: Endpoint, spec: ParsedSpec) -> str:
    """Generate a copy-pasteable curl command for an endpoint."""
    return generate_curl(endpoint, spec)


def format_endpoint_detail_output(tree_output: str, curl_command: str) -> str:
    """Combine tree output and curl with exactly one blank line separator."""
    tree_output = tree_output.rstrip("\n")
    curl_command = curl_command.rstrip("\n")
    if not curl_command:
        return tree_output
    return f"{tree_output}\n\n{curl_command}"


def render_endpoint_detail_output(
    endpoint: Endpoint,
    spec: ParsedSpec,
    *,
    console: Console | None = None,
    width: int = 120,
) -> str:
    """Render endpoint detail tree and curl as a single copy-pasteable block."""
    curl_command = build_endpoint_curl_command(endpoint, spec)
    root = build_endpoint_tree(endpoint, spec)
    title = f"{endpoint.method} {endpoint.path}"
    tree_output = render_detail_tree_to_text(
        root,
        title,
        console=console,
        width=width,
    )
    return format_endpoint_detail_output(tree_output, curl_command)


def extract_curl_block_from_detail_output(detail_output: str) -> str:
    """Return the curl portion from combined endpoint detail output."""
    parts = detail_output.split("\n\n", 1)
    if len(parts) == 2:
        return parts[1]
    return ""


def curl_block_is_copy_pasteable(curl_block: str) -> bool:
    """Return True when curl output is raw text suitable for direct copy-paste."""
    if not curl_block.strip():
        return False
    lowered = curl_block.lower()
    if "curl command:" in lowered:
        return False
    if "--- curl ---" in lowered:
        return False
    if any(character in curl_block for character in TREE_PREFIX_CHARACTERS):
        return False
    return curl_block.lstrip().startswith("curl ")


def open_endpoint_detail_view(endpoint: Endpoint, spec: ParsedSpec) -> None:
    """Open endpoint drill-down with curl rendered below the tree."""
    curl_command = build_endpoint_curl_command(endpoint, spec)
    root = build_endpoint_tree(endpoint, spec)
    title = f"{endpoint.method} {endpoint.path}"
    run_interactive_tree(root, title, curl_command=curl_command, clear_screen=True)


def repl_interaction_mode(*, context_enabled: bool, browsing: bool = False) -> str:
    """Return the interaction mode label for the REPL status footer."""
    if browsing:
        return "Browse"
    if context_enabled:
        return "Context"
    return "Interactive"


def truncate_source_middle(source: str, max_length: int) -> str:
    """Truncate long sources in the middle, preserving start and end segments."""
    if max_length <= 0 or len(source) <= max_length:
        return source

    ellipsis = "..."
    if max_length <= len(ellipsis):
        return source[:max_length]

    available = max_length - len(ellipsis)
    prefix_len = available // 2
    suffix_len = available - prefix_len
    return f"{source[:prefix_len]}{ellipsis}{source[-suffix_len:]}"


def format_repl_status_line(
    api_source: str,
    mode: str,
    *,
    terminal_width: int | None = None,
) -> str:
    """Format the persistent REPL footer: ``API: <source> | Mode: <mode>``."""
    prefix = "API: "
    separator = " | Mode: "
    fixed_len = len(prefix) + len(separator) + len(mode)
    max_source_len = 80
    if terminal_width is not None and terminal_width > fixed_len + 1:
        max_source_len = max(terminal_width - fixed_len, 1)

    display_source = truncate_source_middle(api_source, max_source_len)
    return f"{prefix}{display_source}{separator}{mode}"


def render_repl_status_line(
    api_source: str,
    mode: str,
    *,
    terminal_width: int | None = None,
) -> str:
    """Render the REPL footer status line text."""
    return format_repl_status_line(
        api_source,
        mode,
        terminal_width=terminal_width,
    )


def build_repl_bottom_toolbar(
    api_source: str,
    mode: str,
    *,
    terminal_width: int,
    color_enabled: bool,
):
    """Build the REPL bottom toolbar with transparent status-line styling."""
    from restx.cli.colors import (
        build_repl_status_toolbar,
        format_repl_status_separator,
    )

    status_line = format_repl_status_line(
        api_source,
        mode,
        terminal_width=terminal_width,
    )
    if not color_enabled:
        separator = format_repl_status_separator(terminal_width=terminal_width)
        return f"{separator}\n{status_line}"
    return build_repl_status_toolbar(status_line, terminal_width=terminal_width)


def format_detail_view_header(index: int, endpoint: Endpoint) -> str:
    """Format the non-destructive detail view header for a match selection."""
    return (
        f"--- Detail view for [{index}] "
        f"{endpoint.method} {endpoint.path} ---"
    )


def run_repl_detail_view(
    index: int,
    endpoint: Endpoint,
    spec: ParsedSpec,
    console: Console,
) -> None:
    """Render endpoint detail below the match list without clearing the screen."""
    console.print()
    console.print(format_detail_view_header(index, endpoint))
    curl_command = build_endpoint_curl_command(endpoint, spec)
    root = build_endpoint_tree(endpoint, spec)
    title = f"{endpoint.method} {endpoint.path}"
    run_interactive_tree(
        root,
        title,
        curl_command=curl_command,
        clear_screen=False,
    )


from restx.cli.match_list import (  # noqa: E402
    format_match_list_lines,
)
PAGER_TITLE = "API Endpoints"
MATCH_LIST_PAGER_TITLE = "Match results"
PAGER_STATUS_HELP = (
    "/ search  ? reverse  ↑/↓ scroll  Space page  Ctrl+B/F page  q quit"
)
KEY_PAGE_UP = "\x1b[5~"
KEY_PAGE_DOWN = "\x1b[6~"
KEY_CTRL_B = "\x02"
KEY_CTRL_F = "\x06"
KEY_SPACE = " "
PAGER_SEARCH_FORWARD_HELP = "Enter search  Esc cancel"
PAGER_SEARCH_ACTIVE_HELP = "n/N next/prev match  Esc clear search"


def _pager_page_height() -> int:
    try:
        rows = shutil.get_terminal_size(fallback=(80, 24)).lines
    except OSError:
        rows = 24
    return max(rows - 2, 1)


def _parse_pager_endpoint_line(
    stripped: str,
) -> tuple[str, str, str | None, str] | None:
    """Parse method, path, summary, and padding between method and path."""
    for candidate in sorted(METHOD_STYLES, key=len, reverse=True):
        if not stripped.startswith(candidate):
            continue
        after_method = stripped[len(candidate) :]
        if not after_method:
            return None
        slash_index = after_method.find("/")
        if slash_index < 0:
            return None
        padding = after_method[:slash_index]
        if not padding or not all(character == " " for character in padding):
            return None
        path_rest = after_method[slash_index:]
        summary_sep = " — "
        if summary_sep in path_rest:
            path_part, summary = path_rest.split(summary_sep, 1)
            return candidate, path_part, summary, padding
        return candidate, path_rest, None, padding
    return None


def _format_pager_line(
    line: str,
    *,
    highlighted: bool = False,
    current: bool = False,
) -> str:
    """Apply method/path coloring to a pager buffer line."""
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]

    parsed = _parse_pager_endpoint_line(stripped)
    if parsed is None:
        if current:
            return f"[{PAGER_SELECTED_STYLE}]{line}[/{PAGER_SELECTED_STYLE}]"
        if highlighted:
            return f"[{SEARCH_HIGHLIGHT_STYLE}]{line}[/{SEARCH_HIGHLIGHT_STYLE}]"
        return f"[bold {PATH_STYLE}]{line}[/bold {PATH_STYLE}]"

    method, path_part, summary, padding = parsed
    method_style = METHOD_STYLES.get(method, BASE_TEXT_STYLE)
    if summary:
        body = (
            f"[{method_style}]{method}[/{method_style}]{padding}"
            f"[{PATH_STYLE}]{path_part}[/{PATH_STYLE}] — {summary}"
        )
    else:
        body = (
            f"[{method_style}]{method}[/{method_style}]{padding}"
            f"[{PATH_STYLE}]{path_part}[/{PATH_STYLE}]"
        )

    rendered = f"{indent}{body}"
    if current:
        return f"[{PAGER_SELECTED_STYLE}]{rendered}[/{PAGER_SELECTED_STYLE}]"
    if highlighted:
        return f"[{SEARCH_HIGHLIGHT_STYLE}]{rendered}[/{SEARCH_HIGHLIGHT_STYLE}]"
    return rendered


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


def _rich_style(text: str, style: str) -> str:
    """Wrap plain text in Rich markup, skipping empty styles."""
    if not style:
        return text
    return f"[{style}]{text}[/{style}]"


def _format_match_list_params_markup(params: str) -> str:
    parts: list[str] = [" params: "]
    for part_index, part in enumerate(_split_param_parts(params)):
        if part_index:
            parts.append(", ")
        name, suffix = _split_param_name(part)
        if PARAM_NAME_STYLE:
            parts.append(_rich_style(name, PARAM_NAME_STYLE))
        else:
            parts.append(name)
        if suffix:
            parts.append(suffix)
    return "".join(parts)


def _parse_match_list_pager_line(
    stripped: str,
) -> tuple[str, str, str, str | None] | None:
    """Parse ``[index] method path_part params`` from a match list line."""
    match = re.match(r"\[(\d+)\]\s+(\S+)(.*)$", stripped)
    if not match:
        return None

    index, method, rest = match.groups()
    params_sep = " params: "
    if params_sep in rest:
        path_part, params = rest.split(params_sep, 1)
        return index, method, path_part, params
    return index, method, rest, None


def _format_match_list_pager_line(
    line: str,
    *,
    highlighted: bool = False,
    current: bool = False,
) -> str:
    """Apply match-list coloring to a pager buffer line."""
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]

    if stripped.endswith("matches:") or stripped.endswith("match:"):
        header_style = MATCH_HEADER_STYLE or "yellow"
        rendered = f"[{header_style}]{stripped}[/{header_style}]"
        if current:
            return f"[{PAGER_SELECTED_STYLE}]{rendered}[/{PAGER_SELECTED_STYLE}]"
        if highlighted:
            return f"[{SEARCH_HIGHLIGHT_STYLE}]{rendered}[/{SEARCH_HIGHLIGHT_STYLE}]"
        return f"{indent}{rendered}"

    parsed = _parse_match_list_pager_line(stripped)
    if parsed is None:
        if current:
            return f"[{PAGER_SELECTED_STYLE}]{line}[/{PAGER_SELECTED_STYLE}]"
        if highlighted:
            return f"[{SEARCH_HIGHLIGHT_STYLE}]{line}[/{SEARCH_HIGHLIGHT_STYLE}]"
        return line

    index, method, path_part, params = parsed
    method_style = METHOD_STYLES.get(method, BASE_TEXT_STYLE)
    slash_index = path_part.find("/")
    if slash_index >= 0:
        padding = path_part[:slash_index]
        path = path_part[slash_index:]
    else:
        padding = path_part
        path = ""

    body = (
        f"[{index}] "
        f"{_rich_style(method, method_style)}"
        f"{padding}{_rich_style(path, PATH_STYLE)}"
    )
    if params:
        body += _format_match_list_params_markup(params)

    rendered = f"{indent}{body}"
    if current:
        return f"[{PAGER_SELECTED_STYLE}]{rendered}[/{PAGER_SELECTED_STYLE}]"
    if highlighted:
        return f"[{SEARCH_HIGHLIGHT_STYLE}]{rendered}[/{SEARCH_HIGHLIGHT_STYLE}]"
    return rendered


PagerLineFormatter = Callable[..., str]


def _format_pager_status(
    pager: Pager,
    *,
    search_input_mode: bool,
    search_input_buffer: str,
    search_forward: bool,
    title: str = PAGER_TITLE,
) -> str:
    if search_input_mode:
        prompt = "/" if search_forward else "?"
        return f"{prompt}{search_input_buffer}  {PAGER_SEARCH_FORWARD_HELP}"

    if pager.search.active:
        total = len(pager.search.match_line_indices)
        current = pager.search.current_match + 1 if total else 0
        if total:
            return (
                f"Search: {pager.search.pattern!r}  "
                f"Match {current}/{total}  "
                f"{PAGER_SEARCH_ACTIVE_HELP}"
            )
        return f"No matches for {pager.search.pattern!r}  Esc clear search"

    position = pager.scroll_offset + 1
    total = max(pager.total_lines, 1)
    end = min(pager.scroll_offset + pager.page_height, pager.total_lines)
    if pager.total_lines == 0:
        range_label = "0 lines"
    else:
        range_label = f"lines {position}-{end} of {total}"
    return f"{title}  {range_label}  {PAGER_STATUS_HELP}"


def _pager_formatted_content_lines(
    pager: Pager,
    *,
    search_input_mode: bool,
    search_input_buffer: str,
    search_forward: bool,
    format_line: PagerLineFormatter = _format_pager_line,
) -> list[str]:
    highlighted = pager.highlighted_line_indices()
    current_match = pager.current_match_line()
    formatted: list[str] = []
    for index, line in enumerate(pager.visible_lines()):
        global_index = pager.scroll_offset + index
        formatted.append(
            format_line(
                line,
                highlighted=global_index in highlighted,
                current=global_index == current_match,
            )
        )
    return formatted


@dataclass(frozen=True)
class PagerDisplayState:
    """Snapshot of rendered pager lines for delta updates."""

    scroll_offset: int
    content_lines: tuple[str, ...]
    status_line: str


def collect_pager_display_state(
    pager: Pager,
    *,
    search_input_mode: bool = False,
    search_input_buffer: str = "",
    search_forward: bool = True,
    format_line: PagerLineFormatter = _format_pager_line,
    title: str = PAGER_TITLE,
) -> PagerDisplayState:
    """Build the current pager viewport snapshot (for rendering and tests)."""
    return PagerDisplayState(
        scroll_offset=pager.scroll_offset,
        content_lines=tuple(
            _pager_formatted_content_lines(
                pager,
                search_input_mode=search_input_mode,
                search_input_buffer=search_input_buffer,
                search_forward=search_forward,
                format_line=format_line,
            )
        ),
        status_line=_format_pager_status(
            pager,
            search_input_mode=search_input_mode,
            search_input_buffer=search_input_buffer,
            search_forward=search_forward,
            title=title,
        ),
    )


def handle_pager_navigation_key(pager: Pager, key: str) -> bool:
    """Apply a pager scroll key. Returns True when the key was handled."""
    if key == KEY_UP:
        pager.scroll_up()
        return True
    if key == KEY_DOWN:
        pager.scroll_down()
        return True
    if key in {KEY_PAGE_UP, KEY_CTRL_B}:
        pager.scroll_page_up()
        return True
    if key in {KEY_PAGE_DOWN, KEY_CTRL_F, KEY_SPACE}:
        pager.scroll_page_down()
        return True
    return False


def _pager_cursor_home() -> None:
    sys.stdout.write("\x1b[H")
    sys.stdout.flush()


def _pager_scroll_viewport_up() -> None:
    sys.stdout.write("\x1b[S")
    sys.stdout.flush()


def _pager_scroll_viewport_down() -> None:
    sys.stdout.write("\x1b[T")
    sys.stdout.flush()


def _pager_write_line(console: Console, row: int, text: str, *, style: str | None = None) -> None:
    sys.stdout.write(f"\x1b[{row};1H\x1b[K")
    if style:
        with console.capture() as capture:
            console.print(text, style=style, highlight=False, end="")
        sys.stdout.write(capture.get())
    else:
        with console.capture() as capture:
            console.print(text, highlight=False, end="")
        sys.stdout.write(capture.get())
    sys.stdout.flush()


def _print_pager_display_state(
    console: Console,
    state: PagerDisplayState,
    *,
    trailing_blank: bool = False,
) -> None:
    for index, line in enumerate(state.content_lines, start=1):
        _pager_write_line(console, index, line)
    status_row = len(state.content_lines) + 2
    _pager_write_line(console, status_row, state.status_line, style=PAGER_FOOTER_STYLE)
    if trailing_blank:
        console.print()


def _print_pager_content(
    console: Console,
    pager: Pager,
    *,
    search_input_mode: bool,
    search_input_buffer: str,
    search_forward: bool,
    trailing_blank: bool = False,
    format_line: PagerLineFormatter = _format_pager_line,
    title: str = PAGER_TITLE,
) -> None:
    state = collect_pager_display_state(
        pager,
        search_input_mode=search_input_mode,
        search_input_buffer=search_input_buffer,
        search_forward=search_forward,
        format_line=format_line,
        title=title,
    )
    _print_pager_display_state(console, state, trailing_blank=trailing_blank)


def _render_pager_delta(
    console: Console,
    pager: Pager,
    *,
    search_input_mode: bool,
    search_input_buffer: str,
    search_forward: bool,
    previous: PagerDisplayState | None,
    initial: bool = False,
    format_line: PagerLineFormatter = _format_pager_line,
    title: str = PAGER_TITLE,
) -> PagerDisplayState:
    """Redraw the pager viewport without full-screen clear when possible."""
    state = collect_pager_display_state(
        pager,
        search_input_mode=search_input_mode,
        search_input_buffer=search_input_buffer,
        search_forward=search_forward,
        format_line=format_line,
        title=title,
    )

    if initial or previous is None:
        console.clear()
        _print_pager_display_state(console, state)
        return state

    scroll_delta = state.scroll_offset - previous.scroll_offset
    content_rows = len(state.content_lines)
    status_row = content_rows + 2
    can_scroll_line = (
        not search_input_mode
        and not pager.search.active
        and content_rows > 0
        and len(previous.content_lines) == content_rows
    )

    if can_scroll_line and scroll_delta == 1:
        _pager_scroll_viewport_up()
        _pager_write_line(console, content_rows, state.content_lines[-1])
        if state.status_line != previous.status_line:
            _pager_write_line(console, status_row, state.status_line, style=PAGER_FOOTER_STYLE)
        return state

    if can_scroll_line and scroll_delta == -1:
        _pager_scroll_viewport_down()
        _pager_write_line(console, 1, state.content_lines[0])
        if state.status_line != previous.status_line:
            _pager_write_line(console, status_row, state.status_line, style=PAGER_FOOTER_STYLE)
        return state

    changed_rows: list[tuple[int, str, str | None]] = []
    for index, line in enumerate(state.content_lines, start=1):
        if index > len(previous.content_lines) or line != previous.content_lines[index - 1]:
            changed_rows.append((index, line, None))
    if state.status_line != previous.status_line:
        changed_rows.append((status_row, state.status_line, PAGER_FOOTER_STYLE))

    if len(changed_rows) >= content_rows:
        _pager_cursor_home()
        _print_pager_display_state(console, state)
        return state

    for row, text, style in changed_rows:
        _pager_write_line(console, row, text, style=style)
    return state


def _terminal_width() -> int:
    try:
        return shutil.get_terminal_size(fallback=(80, 24)).columns
    except OSError:
        return 80


def build_match_list_pager_buffer(
    matches: list[Endpoint],
    *,
    terminal_width: int | None = None,
) -> list[str]:
    """Build the full plain-text buffer for a match list pager session."""
    count = len(matches)
    label = "match" if count == 1 else "matches"
    header = f"  {count} {label}:"
    body_lines = format_match_list_lines(
        matches,
        terminal_width=terminal_width,
    )
    return [header, *body_lines]


def match_results_need_pager(
    matches: list[Endpoint],
    *,
    terminal_width: int | None = None,
    page_height: int | None = None,
) -> bool:
    """Return True when match results exceed one pager viewport."""
    if not matches or not sys.stdout.isatty():
        return False
    if page_height is None:
        page_height = _pager_page_height()
    buffer_lines = build_match_list_pager_buffer(
        matches,
        terminal_width=terminal_width,
    )
    return len(buffer_lines) > page_height


def run_buffer_pager_session(
    pager: Pager,
    console: Console,
    *,
    format_line: PagerLineFormatter = _format_pager_line,
    title: str = PAGER_TITLE,
) -> None:
    """Open a full-screen pager for a pre-built line buffer until the user presses q."""
    if not sys.stdin.isatty():
        for line in pager.buffer:
            console.print(format_line(line))
        return

    search_input_mode = False
    search_input_buffer = ""
    search_forward = True
    exited_interactively = False

    saved_settings = None
    termios_module = None
    try:
        import termios as termios_module

        saved_settings = termios_module.tcgetattr(sys.stdin.fileno())
    except (ImportError, AttributeError):
        termios_module = None
        saved_settings = None
    except Exception:
        saved_settings = None

    try:
        sys.stdout.write("\x1b[?1049h")
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()

        display_state: PagerDisplayState | None = None
        first_frame = True

        while True:
            display_state = _render_pager_delta(
                console,
                pager,
                search_input_mode=search_input_mode,
                search_input_buffer=search_input_buffer,
                search_forward=search_forward,
                previous=display_state,
                initial=first_frame,
                format_line=format_line,
                title=title,
            )
            first_frame = False

            key = _read_key()
            if search_input_mode:
                if key in {"\r", "\n"}:
                    search_input_mode = False
                    if search_input_buffer:
                        pager.start_search(forward=search_forward)
                        pager.set_search_pattern(search_input_buffer)
                    search_input_buffer = ""
                    continue
                if is_escape_key(key) or key == "\x03":
                    search_input_mode = False
                    search_input_buffer = ""
                    continue
                if key in {"\x7f", "\x08"}:
                    search_input_buffer = search_input_buffer[:-1]
                    continue
                if len(key) == 1 and key.isprintable():
                    search_input_buffer += key
                    continue
                continue

            if key in {"q", "Q"}:
                exited_interactively = True
                break
            if handle_pager_navigation_key(pager, key):
                continue
            if key == "/":
                search_input_mode = True
                search_forward = True
                search_input_buffer = ""
                continue
            if key == "?":
                search_input_mode = True
                search_forward = False
                search_input_buffer = ""
                continue
            if pager.search.active:
                if key == "n":
                    pager.search_next()
                    continue
                if key == "N":
                    pager.search_previous()
                    continue
                if is_escape_key(key):
                    pager.clear_search()
                    continue
    finally:
        sys.stdout.write("\x1b[?1049l")
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()
        if exited_interactively:
            _print_pager_content(
                console,
                pager,
                search_input_mode=search_input_mode,
                search_input_buffer=search_input_buffer,
                search_forward=search_forward,
                trailing_blank=True,
                format_line=format_line,
                title=title,
            )
        if saved_settings is not None and termios_module is not None:
            try:
                termios_module.tcsetattr(
                    sys.stdin.fileno(),
                    termios_module.TCSADRAIN,
                    saved_settings,
                )
            except Exception:
                pass


def run_pager_session(
    spec: ParsedSpec,
    console: Console,
    *,
    filter_pattern: str | None = None,
) -> None:
    """Open the full-screen API pager until the user presses q."""
    pager = Pager(
        spec,
        page_height=_pager_page_height(),
        filter_pattern=filter_pattern,
    )
    run_buffer_pager_session(
        pager,
        console,
        format_line=_format_pager_line,
        title=PAGER_TITLE,
    )


def run_match_list_pager_session(
    matches: list[Endpoint],
    console: Console,
) -> None:
    """Page through match results using the shared full-screen pager engine."""
    terminal_width = _terminal_width()
    buffer_lines = build_match_list_pager_buffer(
        matches,
        terminal_width=terminal_width,
    )
    pager = Pager.from_lines(buffer_lines, page_height=_pager_page_height())
    run_buffer_pager_session(
        pager,
        console,
        format_line=_format_match_list_pager_line,
        title=MATCH_LIST_PAGER_TITLE,
    )


def display_match_results(console: Console, matches: list[Endpoint]) -> None:
    """Print match results, paging through the full buffer when needed."""
    if not matches:
        console.print(format_match_results_text(matches))
        return

    terminal_width = _terminal_width() if sys.stdout.isatty() else None
    if match_results_need_pager(matches, terminal_width=terminal_width):
        run_match_list_pager_session(matches, console)
        return

    console.print(
        format_match_results_text(
            matches,
            terminal_width=terminal_width,
        )
    )

