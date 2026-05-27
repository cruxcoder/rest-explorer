"""Full-screen API pager engine (core layer)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .models import Endpoint, ParsedSpec
from .viewer import (
    ApiTreeNode,
    build_api_tree,
    match_viewer_search,
)


class PagerSearchDirection(str, Enum):
    FORWARD = "forward"
    BACKWARD = "backward"


@dataclass
class PagerSearchState:
    """Tracks search mode and matches within the pager buffer."""

    active: bool = False
    pattern: str = ""
    direction: PagerSearchDirection = PagerSearchDirection.FORWARD
    case_sensitive: bool = False
    match_line_indices: list[int] = field(default_factory=list)
    current_match: int = 0


def _filter_endpoints(spec: ParsedSpec, pattern: str | None) -> list[Endpoint]:
    if not pattern:
        return list(spec.endpoints)

    from .curl_generator import resolve_endpoint

    return resolve_endpoint(pattern, spec)


def _filter_tree(roots: list[ApiTreeNode], allowed_paths: set[str]) -> list[ApiTreeNode]:
    filtered: list[ApiTreeNode] = []

    def walk(node: ApiTreeNode) -> ApiTreeNode | None:
        endpoints = [endpoint for endpoint in node.endpoints if endpoint.path in allowed_paths]
        children: list[ApiTreeNode] = []
        for child in node.children:
            filtered_child = walk(child)
            if filtered_child is not None:
                children.append(filtered_child)

        if not endpoints and not children:
            return None

        return ApiTreeNode(
            path=node.path,
            endpoints=endpoints,
            children=children,
            expanded=True,
        )

    for root in roots:
        filtered_root = walk(root)
        if filtered_root is not None:
            filtered.append(filtered_root)
    return filtered



def _render_endpoint_group_lines(
    endpoints: list[Endpoint],
    *,
    prefix: str,
) -> list[str]:
    """Render sibling endpoints with per-group method/path column alignment."""
    if not endpoints:
        return []

    max_method_len = max(len(endpoint.method) for endpoint in endpoints)
    lines: list[str] = []
    for endpoint in endpoints:
        padding = max_method_len - len(endpoint.method)
        body = f"{endpoint.method}{' ' * padding} {endpoint.path}"
        summary = endpoint.summary or ""
        if summary:
            lines.append(f"{prefix}{body} — {summary}")
        else:
            lines.append(f"{prefix}{body}")
    return lines


def render_api_tree_lines(roots: list[ApiTreeNode], *, indent: str = "  ") -> list[str]:
    """Render the API hierarchy into a flat text buffer."""
    lines: list[str] = []

    def walk(node: ApiTreeNode, depth: int) -> None:
        prefix = indent * depth
        lines.append(f"{prefix}{node.path}")
        endpoint_prefix = prefix + indent
        lines.extend(
            _render_endpoint_group_lines(node.endpoints, prefix=endpoint_prefix)
        )
        for child in node.children:
            walk(child, depth + 1)

    first_root = True
    for root in roots:
        if not first_root:
            lines.append("")
        walk(root, 0)
        first_root = False
    return lines


def find_pager_search_matches(
    lines: list[str],
    pattern: str,
    *,
    case_sensitive: bool = False,
) -> list[int]:
    """Return line indices that match a pager search pattern."""
    if not pattern:
        return []

    matches: list[int] = []
    for index, line in enumerate(lines):
        if match_viewer_search(line, pattern, case_sensitive=case_sensitive):
            matches.append(index)
    return matches


class Pager:
    """Paginated, searchable renderer for the API tree."""

    def __init__(
        self,
        spec: ParsedSpec,
        *,
        page_height: int = 24,
        filter_pattern: str | None = None,
    ) -> None:
        self.spec = spec
        self.page_height = max(1, page_height)
        self.filter_pattern = filter_pattern
        self.scroll_offset = 0
        self.search = PagerSearchState()
        self._buffer: list[str] = []
        self._roots: list[ApiTreeNode] = []
        self.render()

    @classmethod
    def from_lines(
        cls,
        lines: list[str],
        *,
        page_height: int = 24,
    ) -> Pager:
        """Build a pager backed by a pre-rendered line buffer."""
        pager = object.__new__(cls)
        pager.spec = None  # type: ignore[assignment]
        pager.page_height = max(1, page_height)
        pager.filter_pattern = None
        pager.scroll_offset = 0
        pager.search = PagerSearchState()
        pager._buffer = list(lines)
        pager._roots = []
        return pager

    @property
    def buffer(self) -> list[str]:
        return list(self._buffer)

    @property
    def total_lines(self) -> int:
        return len(self._buffer)

    @property
    def max_scroll_offset(self) -> int:
        if not self._buffer:
            return 0
        return max(0, len(self._buffer) - self.page_height)

    def render(self) -> list[str]:
        """Render the API tree into the internal buffer."""
        roots = build_api_tree(self.spec)
        if self.filter_pattern:
            allowed_paths = {endpoint.path for endpoint in _filter_endpoints(self.spec, self.filter_pattern)}
            roots = _filter_tree(roots, allowed_paths)
        self._roots = roots
        self._buffer = render_api_tree_lines(roots)
        self.scroll_offset = min(self.scroll_offset, self.max_scroll_offset)
        if self.search.active and self.search.pattern:
            self._refresh_search_matches()
        return list(self._buffer)

    def visible_lines(self) -> list[str]:
        """Return the lines visible on the current page."""
        if not self._buffer:
            return []
        end = self.scroll_offset + self.page_height
        return self._buffer[self.scroll_offset:end]

    def scroll_up(self, lines: int = 1) -> None:
        self.scroll_offset = max(0, self.scroll_offset - max(1, lines))

    def scroll_down(self, lines: int = 1) -> None:
        self.scroll_offset = min(self.max_scroll_offset, self.scroll_offset + max(1, lines))

    @property
    def half_page_lines(self) -> int:
        return max(1, self.page_height // 2)

    def scroll_page_up(self) -> None:
        self.scroll_up(self.page_height)

    def scroll_page_down(self) -> None:
        self.scroll_down(self.page_height)

    def scroll_half_page_up(self) -> None:
        self.scroll_up(self.half_page_lines)

    def scroll_half_page_down(self) -> None:
        self.scroll_down(self.half_page_lines)

    def at_top(self) -> bool:
        return self.scroll_offset <= 0

    def at_bottom(self) -> bool:
        return self.scroll_offset >= self.max_scroll_offset

    def start_search(self, *, forward: bool = True) -> None:
        direction = PagerSearchDirection.FORWARD if forward else PagerSearchDirection.BACKWARD
        self.search = PagerSearchState(direction=direction)

    def set_search_pattern(self, pattern: str) -> None:
        self.search.active = bool(pattern)
        self.search.pattern = pattern
        self._refresh_search_matches()

    def clear_search(self) -> None:
        self.search = PagerSearchState()

    def search_next(self) -> None:
        if not self.search.active or not self.search.match_line_indices:
            return
        count = len(self.search.match_line_indices)
        if self.search.direction is PagerSearchDirection.FORWARD:
            self.search.current_match = (self.search.current_match + 1) % count
        else:
            self.search.current_match = (self.search.current_match - 1) % count
        self._scroll_to_current_match()

    def search_previous(self) -> None:
        if not self.search.active or not self.search.match_line_indices:
            return
        count = len(self.search.match_line_indices)
        if self.search.direction is PagerSearchDirection.FORWARD:
            self.search.current_match = (self.search.current_match - 1) % count
        else:
            self.search.current_match = (self.search.current_match + 1) % count
        self._scroll_to_current_match()

    def current_match_line(self) -> int | None:
        if not self.search.active or not self.search.match_line_indices:
            return None
        return self.search.match_line_indices[self.search.current_match]

    def highlighted_line_indices(self) -> set[int]:
        if not self.search.active:
            return set()
        return set(self.search.match_line_indices)

    def _refresh_search_matches(self) -> None:
        self.search.match_line_indices = find_pager_search_matches(
            self._buffer,
            self.search.pattern,
            case_sensitive=self.search.case_sensitive,
        )
        if not self.search.match_line_indices:
            self.search.current_match = 0
            return

        if self.search.direction is PagerSearchDirection.FORWARD:
            self.search.current_match = 0
        else:
            self.search.current_match = len(self.search.match_line_indices) - 1
        self._scroll_to_current_match()

    def _scroll_to_current_match(self) -> None:
        line_index = self.current_match_line()
        if line_index is None:
            return
        if line_index < self.scroll_offset:
            self.scroll_offset = line_index
        elif line_index >= self.scroll_offset + self.page_height:
            self.scroll_offset = min(
                line_index - self.page_height + 1,
                self.max_scroll_offset,
            )
