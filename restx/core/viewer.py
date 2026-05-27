"""API tree model and search engine for the interactive viewer (core layer)."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from enum import Enum

from restx.core.models import Endpoint, ParsedSpec


@dataclass
class ApiTreeNode:
    """A node in the hierarchical API path tree."""

    path: str
    endpoints: list[Endpoint] = field(default_factory=list)
    children: list[ApiTreeNode] = field(default_factory=list)
    expanded: bool = False


@dataclass(frozen=True)
class TreeEntry:
    """A searchable, navigable item in the API tree."""

    entry_id: str
    path: str
    label: str
    node: ApiTreeNode | None = None
    endpoint: Endpoint | None = None


class SearchDirection(str, Enum):
    """Direction for initial search placement and n/N navigation."""

    FORWARD = "forward"
    BACKWARD = "backward"


@dataclass
class SearchState:
    """Tracks viewer search mode, matches, and temporary expansions."""

    active: bool = False
    input_mode: bool = False
    input_buffer: str = ""
    direction: SearchDirection = SearchDirection.FORWARD
    pattern: str = ""
    case_sensitive: bool = False
    match_indices: list[int] = field(default_factory=list)
    current_match: int = 0
    temporary_expanded: set[str] = field(default_factory=set)

    @property
    def forward(self) -> bool:
        return self.direction is SearchDirection.FORWARD


def parent_path(path: str) -> str | None:
    """Return the parent path segment for a URL path, or None if root."""
    stripped = path.strip("/")
    if not stripped:
        return None
    parts = stripped.split("/")
    if len(parts) <= 1:
        return None
    return "/" + "/".join(parts[:-1])


def build_api_tree(spec: ParsedSpec) -> list[ApiTreeNode]:
    """Build a hierarchical tree of API paths from a parsed specification."""
    paths = sorted({endpoint.path for endpoint in spec.endpoints})
    nodes: dict[str, ApiTreeNode] = {}

    for path in paths:
        endpoints = sorted(
            (endpoint for endpoint in spec.endpoints if endpoint.path == path),
            key=lambda endpoint: endpoint.method,
        )
        nodes[path] = ApiTreeNode(path=path, endpoints=list(endpoints))

    roots: list[ApiTreeNode] = []
    for path in paths:
        node = nodes[path]
        parent = parent_path(path)
        if parent is not None and parent in nodes:
            nodes[parent].children.append(node)
        else:
            roots.append(node)

    for node in nodes.values():
        node.children.sort(key=lambda child: child.path)
    roots.sort(key=lambda node: node.path)
    return roots


def endpoint_label(endpoint: Endpoint) -> str:
    """Return the display label for an endpoint node."""
    summary = endpoint.summary or ""
    if summary:
        return f"{endpoint.method} {endpoint.path} — {summary}"
    return f"{endpoint.method} {endpoint.path}"


def path_entry_id(path: str) -> str:
    return f"path:{path}"


def endpoint_entry_id(endpoint: Endpoint) -> str:
    return f"endpoint:{endpoint.method}:{endpoint.path}"


def collect_tree_entries(roots: list[ApiTreeNode]) -> list[TreeEntry]:
    """Return all tree entries in display order (fully expanded walk)."""
    entries: list[TreeEntry] = []

    def walk(node: ApiTreeNode) -> None:
        entries.append(
            TreeEntry(
                entry_id=path_entry_id(node.path),
                path=node.path,
                label=node.path,
                node=node,
            )
        )
        for endpoint in node.endpoints:
            entries.append(
                TreeEntry(
                    entry_id=endpoint_entry_id(endpoint),
                    path=endpoint.path,
                    label=endpoint_label(endpoint),
                    endpoint=endpoint,
                )
            )
        for child in node.children:
            walk(child)

    for root in roots:
        walk(root)
    return entries


def flatten_visible_entries(roots: list[ApiTreeNode]) -> list[TreeEntry]:
    """Return entries visible given the current expanded state."""
    entries: list[TreeEntry] = []

    def walk(node: ApiTreeNode) -> None:
        entries.append(
            TreeEntry(
                entry_id=path_entry_id(node.path),
                path=node.path,
                label=node.path,
                node=node,
            )
        )
        for endpoint in node.endpoints:
            entries.append(
                TreeEntry(
                    entry_id=endpoint_entry_id(endpoint),
                    path=endpoint.path,
                    label=endpoint_label(endpoint),
                    endpoint=endpoint,
                )
            )
        if node.expanded:
            for child in node.children:
                walk(child)

    for root in roots:
        walk(root)
    return entries


def collect_root_display_labels(roots: list[ApiTreeNode]) -> list[str]:
    """Return labels visible in the root-only collapsed viewer."""
    labels: list[str] = []
    for node in roots:
        if node.children:
            labels.append(f"▶ {node.path}")
        else:
            labels.append(node.path)
        for endpoint in node.endpoints:
            labels.append(endpoint_label(endpoint))
    return labels


def _pattern_is_regex(pattern: str) -> bool:
    regex_markers = set(".^$+|(")
    return any(char in pattern for char in regex_markers) or "\\" in pattern


def _glob_match(text: str, pattern: str, *, case_sensitive: bool) -> bool:
    """Match text against a glob pattern, including unanchored variants."""
    haystack = text if case_sensitive else text.lower()
    candidates = [pattern if case_sensitive else pattern.lower()]

    if not pattern.startswith("*"):
        candidates.append(f"*{pattern}" if case_sensitive else f"*{pattern.lower()}")
    if "*" in pattern or "?" in pattern:
        wrapped = f"*{pattern}*" if case_sensitive else f"*{pattern.lower()}*"
        if wrapped not in candidates:
            candidates.append(wrapped)

    for candidate in candidates:
        if case_sensitive:
            if fnmatch.fnmatchcase(haystack, candidate):
                return True
        elif fnmatch.fnmatch(haystack, candidate):
            return True
    return False


def match_viewer_search(text: str, pattern: str, *, case_sensitive: bool = False) -> bool:
    """Match viewer search text against a glob or regex pattern."""
    if not pattern:
        return False

    if _pattern_is_regex(pattern):
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            return bool(re.search(pattern, text, flags))
        except re.error:
            pass

    if _glob_match(text, pattern, case_sensitive=case_sensitive):
        return True

    haystack = text if case_sensitive else text.lower()
    needle = pattern if case_sensitive else pattern.lower()
    if "*" not in pattern and "?" not in pattern:
        if needle in haystack:
            return True
        collapsed_haystack = " ".join(haystack.split())
        collapsed_needle = " ".join(needle.split())
        return collapsed_needle in collapsed_haystack
    return False


def find_search_matches(
    entries: list[TreeEntry],
    pattern: str,
    *,
    case_sensitive: bool = False,
) -> list[int]:
    """Return indices of entries matching the search pattern."""
    matches: list[int] = []
    for index, entry in enumerate(entries):
        searchable = (entry.label, entry.path)
        if any(
            match_viewer_search(text, pattern, case_sensitive=case_sensitive)
            for text in searchable
        ):
            matches.append(index)
    return matches


def find_node_by_path(roots: list[ApiTreeNode], path: str) -> ApiTreeNode | None:
    """Locate a path node anywhere in the tree."""

    def walk(nodes: list[ApiTreeNode]) -> ApiTreeNode | None:
        for node in nodes:
            if node.path == path:
                return node
            found = walk(node.children)
            if found is not None:
                return found
        return None

    return walk(roots)


def collect_expansion_paths(entry: TreeEntry) -> list[str]:
    """Return path nodes that must expand to reveal a search match."""
    paths: list[str] = []
    current = entry.path
    while True:
        parent = parent_path(current)
        if parent is None:
            break
        paths.append(parent)
        current = parent
    if entry.endpoint is not None:
        paths.insert(0, entry.path)
    return paths


def clear_temporary_expansions(
    roots: list[ApiTreeNode],
    temporary_expanded: set[str],
) -> None:
    """Re-collapse nodes that were expanded only for search context."""
    for path in temporary_expanded:
        node = find_node_by_path(roots, path)
        if node is not None:
            node.expanded = False


def apply_search_expansions(
    roots: list[ApiTreeNode],
    entry: TreeEntry,
    *,
    previous_expansions: set[str],
) -> set[str]:
    """Expand ancestors for a match; return the new temporary expansion set."""
    clear_temporary_expansions(roots, previous_expansions)
    temporary_expanded: set[str] = set()
    for path in collect_expansion_paths(entry):
        node = find_node_by_path(roots, path)
        if node is None:
            continue
        needs_expansion = bool(node.children) or (
            entry.endpoint is not None and path == entry.path and node.endpoints
        )
        if not needs_expansion:
            continue
        if not node.expanded:
            node.expanded = True
            temporary_expanded.add(path)
    return temporary_expanded


def start_search_input(*, forward: bool = True) -> SearchState:
    """Enter search input mode for forward (/) or backward (?) search."""
    direction = SearchDirection.FORWARD if forward else SearchDirection.BACKWARD
    return SearchState(input_mode=True, direction=direction)


def toggle_search_case_sensitivity(state: SearchState) -> SearchState:
    """Toggle case sensitivity for the active or next search."""
    state.case_sensitive = not state.case_sensitive
    return state


def refresh_search_matches(
    state: SearchState,
    entries: list[TreeEntry] | None = None,
    roots: list[ApiTreeNode] | None = None,
) -> SearchState:
    """Recompute matches after pattern or case-sensitivity changes."""
    if entries is None or roots is None:
        return state

    state.match_indices = find_search_matches(
        entries,
        state.pattern,
        case_sensitive=state.case_sensitive,
    )
    if not state.match_indices:
        state.current_match = 0
        clear_temporary_expansions(roots, state.temporary_expanded)
        state.temporary_expanded = set()
        return state

    state.current_match = _initial_match_index(state)
    entry = entries[state.match_indices[state.current_match]]
    state.temporary_expanded = apply_search_expansions(
        roots,
        entry,
        previous_expansions=state.temporary_expanded,
    )
    return state


def commit_search(
    state: SearchState,
    entries: list[TreeEntry],
    roots: list[ApiTreeNode],
) -> SearchState:
    """Execute a search from the input buffer."""
    state.input_mode = False
    state.pattern = state.input_buffer
    state.input_buffer = ""
    if not state.pattern:
        return SearchState(direction=state.direction, case_sensitive=state.case_sensitive)

    state.active = True
    state.match_indices = find_search_matches(
        entries,
        state.pattern,
        case_sensitive=state.case_sensitive,
    )
    if not state.match_indices:
        state.current_match = 0
        state.temporary_expanded = set()
        return state

    state.current_match = _initial_match_index(state)
    entry = entries[state.match_indices[state.current_match]]
    state.temporary_expanded = apply_search_expansions(
        roots,
        entry,
        previous_expansions=set(),
    )
    return state


def cancel_search_input(state: SearchState) -> SearchState:
    """Leave search input mode without activating a search."""
    return SearchState(
        direction=state.direction,
        case_sensitive=state.case_sensitive,
    )


def exit_search(state: SearchState, roots: list[ApiTreeNode]) -> SearchState:
    """Exit active search, restoring collapsed temporary expansions."""
    clear_temporary_expansions(roots, state.temporary_expanded)
    return SearchState(case_sensitive=state.case_sensitive)


def goto_next_match(
    state: SearchState,
    entries: list[TreeEntry],
    roots: list[ApiTreeNode],
) -> SearchState:
    """Jump to the next search match (n)."""
    if not state.active or not state.match_indices:
        return state
    if state.forward:
        state.current_match = (state.current_match + 1) % len(state.match_indices)
    else:
        state.current_match = (state.current_match - 1) % len(state.match_indices)
    entry = entries[state.match_indices[state.current_match]]
    state.temporary_expanded = apply_search_expansions(
        roots,
        entry,
        previous_expansions=state.temporary_expanded,
    )
    return state


def goto_previous_match(
    state: SearchState,
    entries: list[TreeEntry],
    roots: list[ApiTreeNode],
) -> SearchState:
    """Jump to the previous search match (N)."""
    if not state.active or not state.match_indices:
        return state
    if state.forward:
        state.current_match = (state.current_match - 1) % len(state.match_indices)
    else:
        state.current_match = (state.current_match + 1) % len(state.match_indices)
    entry = entries[state.match_indices[state.current_match]]
    state.temporary_expanded = apply_search_expansions(
        roots,
        entry,
        previous_expansions=state.temporary_expanded,
    )
    return state


def current_search_entry(
    state: SearchState,
    entries: list[TreeEntry],
) -> TreeEntry | None:
    """Return the currently highlighted search match, if any."""
    if not state.active or not state.match_indices:
        return None
    return entries[state.match_indices[state.current_match]]


def search_match_entry_ids(
    state: SearchState,
    entries: list[TreeEntry],
) -> set[str]:
    """Return entry IDs for all active search matches."""
    if not state.active:
        return set()
    return {entries[index].entry_id for index in state.match_indices}


def _initial_match_index(state: SearchState) -> int:
    if state.forward:
        return 0
    return len(state.match_indices) - 1


@dataclass
class SelectionState:
    """Tracks the viewer cursor and inline detail expansion."""

    cursor: int = 0
    detail_expanded_entry_id: str | None = None


def append_path_to_prompt(current: str, path: str) -> str:
    """Append an API path to the REPL prompt, preserving existing text."""
    stripped = current.strip()
    if not stripped:
        return path
    return f"{current.rstrip()} {path}"


def entry_transfer_path(entry: TreeEntry) -> str:
    """Return the API path to transfer for a tree entry."""
    return entry.path


def clamp_selection_index(index: int, entry_count: int) -> int:
    """Clamp a selection index to valid visible-entry bounds."""
    if entry_count <= 0:
        return 0
    return max(0, min(index, entry_count - 1))


def move_selection_up(state: SelectionState, entry_count: int) -> SelectionState:
    """Move the selection cursor up (previous visible entry)."""
    state.cursor = clamp_selection_index(state.cursor - 1, entry_count)
    return state


def move_selection_down(state: SelectionState, entry_count: int) -> SelectionState:
    """Move the selection cursor down (next visible entry)."""
    state.cursor = clamp_selection_index(state.cursor + 1, entry_count)
    return state


def sync_selection_to_entry(
    state: SelectionState,
    visible_entries: list[TreeEntry],
    entry_id: str,
) -> SelectionState:
    """Align the selection cursor with a specific entry id."""
    for index, entry in enumerate(visible_entries):
        if entry.entry_id == entry_id:
            state.cursor = index
            return state
    return state


def selection_active_entry_id(
    state: SelectionState,
    visible_entries: list[TreeEntry],
    *,
    search: SearchState | None = None,
    all_entries: list[TreeEntry] | None = None,
) -> str | None:
    """Return the entry id that should appear selected in the tree."""
    if search is not None and search.active and all_entries is not None:
        current = current_search_entry(search, all_entries)
        if current is not None:
            return current.entry_id
    if not visible_entries:
        return None
    index = clamp_selection_index(state.cursor, len(visible_entries))
    return visible_entries[index].entry_id


def selected_visible_entry(
    state: SelectionState,
    visible_entries: list[TreeEntry],
) -> TreeEntry | None:
    """Return the currently selected visible entry."""
    if not visible_entries:
        return None
    index = clamp_selection_index(state.cursor, len(visible_entries))
    return visible_entries[index]


def enter_transfers_path(search: SearchState) -> bool:
    """Return True when Enter should append a path to the REPL prompt."""
    return search.active


def enter_toggles_detail(search: SearchState) -> bool:
    """Return True when Enter should toggle inline endpoint details."""
    return not search.active and not search.input_mode


def toggle_detail_expansion(
    state: SelectionState,
    entry: TreeEntry,
) -> SelectionState:
    """Toggle inline detail expansion for an endpoint entry."""
    if entry.endpoint is None:
        state.detail_expanded_entry_id = None
        return state
    if state.detail_expanded_entry_id == entry.entry_id:
        state.detail_expanded_entry_id = None
    else:
        state.detail_expanded_entry_id = entry.entry_id
    return state


def collapse_detail_expansion(state: SelectionState) -> SelectionState:
    """Collapse any open inline detail view."""
    state.detail_expanded_entry_id = None
    return state


def toggle_path_node_expansion(entry: TreeEntry) -> None:
    """Expand or collapse a path node in the API tree."""
    if entry.node is None:
        return
    entry.node.expanded = not entry.node.expanded


@dataclass
class ViewerSessionState:
    """Persisted viewer state across open/close toggles within a REPL session."""

    expanded_paths: frozenset[str] = field(default_factory=frozenset)
    selected_entry_id: str | None = None
    cursor: int = 0
    detail_expanded_entry_id: str | None = None
    search_history: tuple[str, ...] = ()
    search_active: bool = False
    search_pattern: str = ""
    search_direction: SearchDirection = SearchDirection.FORWARD
    search_case_sensitive: bool = False
    search_match_entry_id: str | None = None


def collect_expanded_paths(
    roots: list[ApiTreeNode],
    *,
    exclude: set[str] | None = None,
) -> set[str]:
    """Return paths of all expanded nodes, optionally excluding temporary ones."""
    excluded = exclude or set()
    paths: set[str] = set()

    def walk(node: ApiTreeNode) -> None:
        if node.expanded and node.path not in excluded:
            paths.add(node.path)
        for child in node.children:
            walk(child)

    for root in roots:
        walk(root)
    return paths


def apply_expanded_paths(roots: list[ApiTreeNode], paths: set[str]) -> None:
    """Restore expanded/collapsed state for path nodes."""
    all_nodes: list[ApiTreeNode] = []

    def walk(node: ApiTreeNode) -> None:
        all_nodes.append(node)
        for child in node.children:
            walk(child)

    for root in roots:
        walk(root)

    for node in all_nodes:
        node.expanded = node.path in paths


def append_search_history(history: list[str], pattern: str, *, limit: int = 50) -> list[str]:
    """Append a search pattern to history, skipping consecutive duplicates."""
    if not pattern:
        return list(history)
    updated = list(history)
    if updated and updated[-1] == pattern:
        return updated
    updated.append(pattern)
    if len(updated) > limit:
        return updated[-limit:]
    return updated


def search_history_offset(
    history: list[str],
    index: int,
    *,
    forward: bool,
) -> tuple[int, str]:
    """Move through search history; returns (new_index, pattern)."""
    if not history:
        return index, ""
    if index < 0:
        index = len(history)
    if forward:
        index = min(index + 1, len(history))
    else:
        index = max(index - 1, 0)
    if index <= 0:
        return 0, ""
    return index, history[index - 1]


def capture_viewer_session_state(
    roots: list[ApiTreeNode],
    selection: SelectionState,
    search: SearchState,
    visible_entries: list[TreeEntry],
    all_entries: list[TreeEntry],
    *,
    search_history: list[str],
) -> ViewerSessionState:
    """Snapshot viewer state when the split pane closes."""
    expanded = collect_expanded_paths(roots, exclude=search.temporary_expanded)
    selected_entry_id = selection_active_entry_id(
        selection,
        visible_entries,
        search=search,
        all_entries=all_entries,
    )
    search_match_entry_id: str | None = None
    if search.active and search.match_indices:
        match_entry = current_search_entry(search, all_entries)
        if match_entry is not None:
            search_match_entry_id = match_entry.entry_id

    return ViewerSessionState(
        expanded_paths=frozenset(expanded),
        selected_entry_id=selected_entry_id,
        cursor=selection.cursor,
        detail_expanded_entry_id=selection.detail_expanded_entry_id,
        search_history=tuple(search_history),
        search_active=search.active,
        search_pattern=search.pattern,
        search_direction=search.direction,
        search_case_sensitive=search.case_sensitive,
        search_match_entry_id=search_match_entry_id,
    )


def restore_viewer_session_state(
    state: ViewerSessionState,
    roots: list[ApiTreeNode],
) -> tuple[SelectionState, SearchState, list[str]]:
    """Restore selection and search state after reopening the viewer."""
    apply_expanded_paths(roots, set(state.expanded_paths))

    selection = SelectionState(
        cursor=state.cursor,
        detail_expanded_entry_id=state.detail_expanded_entry_id,
    )
    search = SearchState(
        direction=state.search_direction,
        case_sensitive=state.search_case_sensitive,
    )
    search_history = list(state.search_history)

    visible_entries = flatten_visible_entries(roots)
    if state.selected_entry_id is not None:
        selection = sync_selection_to_entry(
            selection,
            visible_entries,
            state.selected_entry_id,
        )
    elif visible_entries:
        selection.cursor = clamp_selection_index(state.cursor, len(visible_entries))

    if state.search_active and state.search_pattern:
        entries = collect_tree_entries(roots)
        search.active = True
        search.pattern = state.search_pattern
        search.match_indices = find_search_matches(
            entries,
            search.pattern,
            case_sensitive=search.case_sensitive,
        )
        if search.match_indices:
            match_idx = _initial_match_index(search)
            if state.search_match_entry_id:
                for idx, entry_index in enumerate(search.match_indices):
                    if entries[entry_index].entry_id == state.search_match_entry_id:
                        match_idx = idx
                        break
            search.current_match = match_idx
            entry = entries[search.match_indices[search.current_match]]
            search.temporary_expanded = apply_search_expansions(
                roots,
                entry,
                previous_expansions=set(),
            )
            visible_entries = flatten_visible_entries(roots)
            target_entry_id = state.search_match_entry_id or entry.entry_id
            selection = sync_selection_to_entry(
                selection,
                visible_entries,
                target_entry_id,
            )

    return selection, search, search_history


def viewer_session_state_to_dict(state: ViewerSessionState) -> dict:
    """Serialize viewer session state to a plain dictionary."""
    return {
        "expanded_paths": sorted(state.expanded_paths),
        "selected_entry_id": state.selected_entry_id,
        "cursor": state.cursor,
        "detail_expanded_entry_id": state.detail_expanded_entry_id,
        "search_history": list(state.search_history),
        "search_active": state.search_active,
        "search_pattern": state.search_pattern,
        "search_direction": state.search_direction.value,
        "search_case_sensitive": state.search_case_sensitive,
        "search_match_entry_id": state.search_match_entry_id,
    }


def viewer_session_state_from_dict(data: dict) -> ViewerSessionState:
    """Deserialize viewer session state from a plain dictionary."""
    direction_value = data.get("search_direction", SearchDirection.FORWARD.value)
    try:
        direction = SearchDirection(direction_value)
    except ValueError:
        direction = SearchDirection.FORWARD

    return ViewerSessionState(
        expanded_paths=frozenset(data.get("expanded_paths", [])),
        selected_entry_id=data.get("selected_entry_id"),
        cursor=int(data.get("cursor", 0)),
        detail_expanded_entry_id=data.get("detail_expanded_entry_id"),
        search_history=tuple(data.get("search_history", [])),
        search_active=bool(data.get("search_active", False)),
        search_pattern=str(data.get("search_pattern", "")),
        search_direction=direction,
        search_case_sensitive=bool(data.get("search_case_sensitive", False)),
        search_match_entry_id=data.get("search_match_entry_id"),
    )
