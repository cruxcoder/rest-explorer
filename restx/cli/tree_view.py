"""Interactive endpoint drill-down tree view."""

from __future__ import annotations

import select
import sys
import termios
import tty
from dataclasses import dataclass, field
from io import StringIO

from rich.console import Console
from rich.tree import Tree

from restx.cli.colors import (
    PAGER_FOOTER_STYLE,
    PAGER_SELECTED_STYLE,
    create_console,
)
from restx.core.matcher import (
    format_schema_field_label,
    schema_node_is_expandable,
    summarize_schema_node,
)
from restx.core.models import Endpoint, Parameter, ParsedSpec, Response, SchemaNode

KEY_UP = "\x1b[A"
KEY_DOWN = "\x1b[B"
KEY_RIGHT = "\x1b[C"
KEY_LEFT = "\x1b[D"

DETAIL_NAVIGATION_HELP = (
    "↑/↓ navigate  ←/→ collapse/expand  Enter toggle  q/Esc exit"
)

DESCRIPTION_ELLIPSIS = "..."


def tree_line_prefix_len(depth: int) -> int:
    """Estimate Rich tree guide prefix length for a node at ``depth``."""
    if depth <= 0:
        return 0
    return 4 * depth


def truncate_description(
    text: str,
    max_width: int,
    *,
    ellipsis: str = DESCRIPTION_ELLIPSIS,
) -> str:
    """Truncate ``text`` to ``max_width`` columns, appending ``ellipsis`` when cut."""
    if max_width <= 0:
        return ""
    if len(text) <= max_width:
        return text
    if max_width <= len(ellipsis):
        return ellipsis[:max_width]
    return text[: max_width - len(ellipsis)] + ellipsis


def truncate_endpoint_summary(
    method: str,
    path: str,
    summary: str,
    *,
    max_line_width: int | None,
    tree_prefix_len: int = 0,
    separator: str = " — ",
) -> str:
    """Truncate an endpoint summary so the rendered line fits the pane width."""
    if not summary or max_line_width is None:
        return summary

    base_len = len(f"{method} {path}{separator}")
    available = max_line_width - tree_prefix_len - base_len
    if available <= 0:
        return ""
    return truncate_description(summary, available)


def is_escape_key(key: str) -> bool:
    """Return True when ``key`` is a standalone Escape press."""
    return key == "\x1b"


@dataclass
class DetailNode:
    """A node in the endpoint detail tree."""

    label: str
    children: list[DetailNode] = field(default_factory=list)
    expanded: bool = True
    is_schema: bool = False


def open_endpoint_view(endpoint: Endpoint, spec: ParsedSpec) -> None:
    """Open the interactive drill-down view for an endpoint."""
    from restx.cli.viewer import open_endpoint_detail_view

    open_endpoint_detail_view(endpoint, spec)


def build_endpoint_tree(endpoint: Endpoint, spec: ParsedSpec) -> DetailNode:
    """Build the endpoint detail tree structure (parameters and responses only)."""
    root = DetailNode(label=f"{endpoint.method} {endpoint.path}")
    root.children.extend(build_endpoint_detail_children(endpoint, spec))
    return root


def build_inline_endpoint_details(endpoint: Endpoint, spec: ParsedSpec) -> DetailNode:
    """Build parameters and response detail nodes for inline viewer expansion."""
    root = DetailNode(label=f"{endpoint.method} {endpoint.path}")
    root.children.extend(build_endpoint_detail_children(endpoint, spec))
    return root


def build_endpoint_detail_children(
    endpoint: Endpoint,
    spec: ParsedSpec,
) -> list[DetailNode]:
    """Build parameters and response child nodes shared by detail views."""
    children: list[DetailNode] = []

    parameters_node = DetailNode(label="Parameters")
    if endpoint.parameters:
        for parameter in endpoint.parameters:
            parameters_node.children.append(_parameter_node(parameter))
    else:
        parameters_node.children.append(DetailNode(label="(none)"))
    children.append(parameters_node)

    for response in _sorted_responses(endpoint.responses):
        children.append(_response_node(response, spec))

    return children


def format_detail_node_label(node: DetailNode) -> str:
    """Return the display label, including expand/collapse indicators."""
    if node.is_schema and node.children:
        prefix = "▼ " if node.expanded else "▶ "
        return f"{prefix}{node.label}"
    return node.label


def toggle_detail_node(node: DetailNode) -> bool:
    """Toggle expansion for a node with children. Returns whether state changed."""
    if not node.children:
        return False
    node.expanded = not node.expanded
    return True


def detail_node_to_rich_tree(node: DetailNode) -> Tree:
    """Convert a detail node subtree into a Rich tree."""
    branch = Tree(format_detail_node_label(node), guide_style="grey50")
    if node.expanded:
        for child in node.children:
            branch.add(detail_node_to_rich_tree(child))
    return branch


def collect_inline_detail_labels(endpoint: Endpoint, spec: ParsedSpec) -> list[str]:
    """Return visible display labels from inline endpoint details (for testing)."""
    return collect_visible_display_labels(build_inline_endpoint_details(endpoint, spec))


def collect_visible_labels(root: DetailNode) -> list[str]:
    """Return labels of visible nodes in display order (for testing)."""
    labels: list[str] = []

    def walk(node: DetailNode) -> None:
        labels.append(node.label)
        if node.expanded:
            for child in node.children:
                walk(child)

    walk(root)
    return labels


def collect_visible_display_labels(root: DetailNode) -> list[str]:
    """Return rendered labels (with expand indicators) in display order."""
    labels: list[str] = []

    def walk(node: DetailNode) -> None:
        labels.append(format_detail_node_label(node))
        if node.expanded:
            for child in node.children:
                walk(child)

    walk(root)
    return labels


def find_schema_node_by_label(root: DetailNode, label: str) -> DetailNode | None:
    """Find the first detail node whose base label matches."""
    if root.label == label:
        return root
    for child in root.children:
        found = find_schema_node_by_label(child, label)
        if found is not None:
            return found
    return None


def _sorted_responses(responses: list[Response]) -> list[Response]:
    def sort_key(response: Response) -> tuple[int, str]:
        code = response.status_code
        if code.isdigit():
            return (0, int(code))
        return (1, code)

    return sorted(responses, key=sort_key)


def _parameter_node(parameter: Parameter) -> DetailNode:
    schema_node = parameter.schema_node
    if schema_node is not None and schema_node_is_expandable(schema_node):
        label = _parameter_schema_label(parameter)
        node = DetailNode(label=label, is_schema=True, expanded=False)
        node.children = _schema_field_detail_nodes(schema_node)
        if parameter.description:
            node.children.append(
                DetailNode(label=f"Description: {parameter.description}")
            )
        return node

    required_label = "required" if parameter.required else "optional"
    param_type = _simple_parameter_type(parameter)
    header = (
        f"{parameter.name} ({parameter.location}, {required_label}, {param_type})"
    )
    node = DetailNode(label=header)
    if parameter.description:
        node.children.append(
            DetailNode(label=f"Description: {parameter.description}")
        )
    return node


def _parameter_schema_label(parameter: Parameter) -> str:
    schema_node = parameter.schema_node
    assert schema_node is not None
    summary = summarize_schema_node(schema_node)
    if summary:
        return f"{parameter.name} ({summary})"
    return parameter.name


def _simple_parameter_type(parameter: Parameter) -> str:
    schema = parameter.schema
    if not isinstance(schema, dict):
        return "string"
    return str(schema.get("type", "string"))


def _response_node(response: Response, spec: ParsedSpec) -> DetailNode:
    node = DetailNode(label=f"Response {response.status_code}")
    schema_node = response.schema_node
    if schema_node is not None and schema_node_is_expandable(schema_node):
        summary = summarize_schema_node(schema_node)
        ref_name = schema_node.ref_name
        if summary:
            label = f"Schema: {summary}"
        elif ref_name:
            label = f"Schema: {ref_name}"
        else:
            label = "Schema"
        schema_detail = DetailNode(label=label, is_schema=True, expanded=False)
        schema_detail.children = _schema_field_detail_nodes(schema_node)
        node.children.append(schema_detail)
        return node

    if schema_node is not None and not schema_node_is_expandable(schema_node):
        type_label = schema_node.type
        if schema_node.format:
            type_label = f"{type_label}, {schema_node.format}"
        node.children.append(DetailNode(label=f"Schema: {type_label}"))
        return node

    if response.description:
        node.children.append(DetailNode(label=response.description))
    return node


def _schema_field_detail_nodes(schema_node: SchemaNode) -> list[DetailNode]:
    """Build immediate child detail nodes for an expandable schema."""
    if schema_node.type == "array" and len(schema_node.children) == 1:
        item = schema_node.children[0]
        if item.children:
            return [_detail_node_from_schema(child) for child in item.children]
        return [_detail_node_from_schema(item)]

    return [_detail_node_from_schema(child) for child in schema_node.children]


def _detail_node_from_schema(schema_node: SchemaNode) -> DetailNode:
    label = format_schema_field_label(schema_node)
    expandable = schema_node_is_expandable(schema_node)
    node = DetailNode(
        label=label,
        is_schema=expandable,
        expanded=False,
    )
    if expandable:
        node.children = _schema_field_detail_nodes(schema_node)
    return node


def render_detail_tree_to_text(
    root: DetailNode,
    title: str,
    *,
    console: Console | None = None,
    cursor: int | None = None,
    flat_nodes: list[DetailNode] | None = None,
    width: int = 120,
) -> str:
    """Render endpoint detail tree to plain text."""
    tree = _render_rich_tree(root, title, cursor, flat_nodes)
    buffer = StringIO()
    render_console = Console(
        file=buffer,
        width=max(width, 1),
        force_terminal=console is not None and not console.no_color,
        no_color=console.no_color if console is not None else True,
        highlight=False,
    )
    render_console.print(tree)
    return buffer.getvalue().rstrip("\n")


def _print_curl_block(console: Console, curl_command: str) -> None:
    """Print curl command as a plain-text block below the tree."""
    if not curl_command:
        return
    console.print()
    console.print(curl_command, highlight=False)
    console.print()
    console.print(DETAIL_NAVIGATION_HELP, style=PAGER_FOOTER_STYLE)
    console.print()


def _build_detail_frame_text(
    root: DetailNode,
    title: str,
    *,
    console: Console,
    cursor: int | None = None,
    flat_nodes: list[DetailNode] | None = None,
    curl_command: str = "",
    width: int = 120,
) -> str:
    """Render the full detail frame as plain text for line counting."""
    tree_part = render_detail_tree_to_text(
        root,
        title,
        console=console,
        cursor=cursor,
        flat_nodes=flat_nodes,
        width=width,
    )
    parts = [tree_part]
    if curl_command:
        parts.append("")
        parts.append(curl_command.rstrip("\n"))
        parts.append("")
    parts.append(DETAIL_NAVIGATION_HELP)
    parts.append("")
    return "\n".join(parts)


def _frame_line_count(frame_text: str) -> int:
    if not frame_text:
        return 0
    return frame_text.count("\n") + (0 if frame_text.endswith("\n") else 1)


def _erase_previous_frame(line_count: int) -> None:
    if line_count <= 0:
        return
    sys.stdout.write(f"\x1b[{line_count}A\x1b[J")
    sys.stdout.flush()


def _print_detail_frame(
    console: Console,
    frame_text: str,
    *,
    dim_help: bool = True,
) -> None:
    """Print a pre-rendered detail frame."""
    for line in frame_text.splitlines():
        if not line:
            console.print()
            continue
        if dim_help and line == DETAIL_NAVIGATION_HELP:
            console.print(line, style=PAGER_FOOTER_STYLE)
        else:
            console.print(line, highlight=False)


def run_interactive_tree(
    root: DetailNode,
    title: str,
    *,
    curl_command: str = "",
    clear_screen: bool = True,
) -> None:
    """Render an interactive tree; return to caller on q or Esc."""
    if not sys.stdin.isatty():
        console = create_console(color_enabled=False)
        console.print(_render_rich_tree(root, title))
        _print_curl_block(console, curl_command)
        return

    console = create_console(color_enabled=True)
    cursor = 0
    flat_nodes: list[DetailNode] = []
    frame_lines = 0

    def rebuild_flat() -> None:
        nonlocal flat_nodes, cursor
        flat_nodes = _flatten_visible(root)
        cursor = min(cursor, max(len(flat_nodes) - 1, 0))

    rebuild_flat()

    while True:
        frame_text = _build_detail_frame_text(
            root,
            title,
            console=console,
            cursor=cursor,
            flat_nodes=flat_nodes,
            curl_command=curl_command,
        )

        if clear_screen:
            console.clear()
        else:
            _erase_previous_frame(frame_lines)

        _print_detail_frame(console, frame_text)
        frame_lines = _frame_line_count(frame_text)

        key = _read_key()
        if key in {"q", "Q", "\x1b", "\x03"}:
            if not clear_screen:
                console.print()
            break
        if key == KEY_UP:
            cursor = max(cursor - 1, 0)
        elif key == KEY_DOWN:
            cursor = min(cursor + 1, len(flat_nodes) - 1)
        elif key in {"\r", "\n"}:
            node = flat_nodes[cursor]
            if toggle_detail_node(node):
                rebuild_flat()
        elif key in {KEY_RIGHT, " "}:
            node = flat_nodes[cursor]
            if node.children:
                node.expanded = True
                rebuild_flat()
        elif key == KEY_LEFT:
            node = flat_nodes[cursor]
            if node.children and node.expanded:
                node.expanded = False
                rebuild_flat()
            else:
                parent = _find_parent(root, node)
                if parent is not None:
                    parent.expanded = False
                    rebuild_flat()
                    if parent in flat_nodes:
                        cursor = flat_nodes.index(parent)


def _flatten_visible(root: DetailNode) -> list[DetailNode]:
    nodes: list[DetailNode] = []

    def walk(node: DetailNode) -> None:
        nodes.append(node)
        if node.expanded:
            for child in node.children:
                walk(child)

    walk(root)
    return nodes


def _find_parent(root: DetailNode, target: DetailNode) -> DetailNode | None:
    for child in root.children:
        if child is target:
            return root
        found = _find_parent(child, target)
        if found is not None:
            return found
    return None


def _render_rich_tree(
    root: DetailNode,
    title: str,
    cursor: int | None = None,
    flat_nodes: list[DetailNode] | None = None,
) -> Tree:
    header = Tree(f"[bold]{title}[/bold]")
    active = flat_nodes[cursor] if flat_nodes is not None and flat_nodes else None

    for child in root.children:
        header.add(_render_node(child, active))

    return header


def _render_node(node: DetailNode, active: DetailNode | None) -> Tree:
    label = format_detail_node_label(node)
    if node is active:
        label = f"[{PAGER_SELECTED_STYLE}]{label}[/{PAGER_SELECTED_STYLE}]"
    branch = Tree(label, hide_root=False)
    if node.expanded:
        for child in node.children:
            branch.add(_render_node(child, active))
    return branch


def _read_key() -> str:
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        char = sys.stdin.read(1)
        if char == "\x1b":
            # Standalone Esc sends only ``\x1b``; arrows send ``\x1b[A``; PgUp ``\x1b[5~``.
            if not select.select([sys.stdin], [], [], 0.05)[0]:
                return char
            sequence = sys.stdin.read(1)
            if sequence != "[":
                return char + sequence
            sequence = "["
            while True:
                next_char = sys.stdin.read(1)
                sequence += next_char
                if next_char.isalpha() or next_char == "~":
                    break
            return char + sequence
        return char
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def parse_selection(line: str) -> int | None:
    """Return a 1-based selection index if line is a select command."""
    stripped = line.strip()
    if stripped.isdigit():
        return int(stripped)

    lower = stripped.lower()
    if lower.startswith("select "):
        suffix = lower[len("select ") :].strip()
        if suffix.isdigit():
            return int(suffix)
    return None


def selection_error(index: int, match_count: int) -> str:
    if match_count == 0:
        return (
            "No matches to select. Run a query first, then choose a number "
            "from the match list."
        )
    return (
        f"Invalid selection: {index}. Choose a number between 1 and "
        f"{match_count} from the latest match list."
    )
