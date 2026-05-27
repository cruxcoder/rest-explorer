"""Contextual query mode (Mode B) state management."""

from __future__ import annotations

from dataclasses import dataclass, field

from .dsl_parser import AndExpr, Expr, parse_query
from .errors import QueryParseError
from .matcher import execute_query, filter_endpoints
from .models import Endpoint, ParsedSpec


@dataclass
class MatchResultState:
    """Last query match list and the currently selected item."""

    matches: list[Endpoint] = field(default_factory=list)
    selected_index: int | None = None
    detail_open: bool = False

    @property
    def has_matches(self) -> bool:
        return bool(self.matches)

    @property
    def match_count(self) -> int:
        return len(self.matches)

    def set_matches(self, matches: list[Endpoint]) -> None:
        """Replace the active match list and clear any open detail view."""
        self.matches = list(matches)
        self.selected_index = None
        self.detail_open = False

    def is_valid_selection(self, index: int) -> bool:
        """Return True when ``index`` is a 1-based match list selection."""
        return 1 <= index <= len(self.matches)

    def select(self, index: int) -> Endpoint | None:
        """Select a match by 1-based index. Returns the endpoint or None."""
        if not self.is_valid_selection(index):
            return None
        self.selected_index = index - 1
        self.detail_open = True
        return self.matches[self.selected_index]

    def selected_endpoint(self) -> Endpoint | None:
        """Return the currently selected endpoint, if any."""
        if self.selected_index is None:
            return None
        return self.matches[self.selected_index]

    def open_detail(self, index: int) -> Endpoint | None:
        """Mark a match selected and detail open without changing the list."""
        return self.select(index)

    def close_detail(self) -> None:
        """Close the detail view while retaining the match list."""
        self.detail_open = False
        self.selected_index = None


@dataclass
class QueryContext:
    """Accumulated filter state for contextual query mode."""

    enabled: bool = False
    filter_parts: list[str] = field(default_factory=list)
    match_results: MatchResultState = field(default_factory=MatchResultState)

    @property
    def filter_text(self) -> str | None:
        if not self.filter_parts:
            return None
        return " AND ".join(self.filter_parts)

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    def reset(self) -> None:
        self.filter_parts.clear()

    def prompt_suffix(self) -> str:
        if not self.enabled:
            return "restx> "
        if self.filter_text:
            return f"restx (context)> [{self.filter_text}] "
        return "restx (context)> "

    def execute(self, query: str, spec: ParsedSpec) -> list[Endpoint]:
        if self.enabled and self.filter_parts:
            combined = parse_query(self.filter_parts[0])
            for part in self.filter_parts[1:]:
                combined = and_expressions(combined, parse_query(part))
            combined = and_expressions(combined, parse_query(query))
            matches = filter_endpoints(combined, spec.endpoints, spec)
        else:
            matches = execute_query(query, spec)

        if self.enabled:
            self.filter_parts.append(query)
        return matches

    def status_lines(self, spec: ParsedSpec) -> list[str]:
        lines = [
            f"Context mode: {'on' if self.enabled else 'off'}",
        ]
        if self.filter_text:
            lines.append(f"Context filter: {self.filter_text}")
        else:
            lines.append("Context filter: (none)")
        lines.extend(
            [
                f"Spec title: {spec.title}",
                f"Spec version: {spec.version}",
                f"OpenAPI version: {spec.openapi_version}",
                f"Endpoint count: {spec.endpoint_count}",
            ]
        )
        return lines


def and_expressions(left: Expr, right: Expr) -> Expr:
    """Combine two parsed expressions with implicit AND."""
    left_terms = list(left.terms) if isinstance(left, AndExpr) else [left]
    right_terms = list(right.terms) if isinstance(right, AndExpr) else [right]
    combined = left_terms + right_terms
    if len(combined) == 1:
        return combined[0]
    return AndExpr(tuple(combined))


def parse_combined_query(context_text: str | None, query: str) -> Expr:
    """Parse a query optionally ANDed with existing context."""
    expr = parse_query(query)
    if not context_text:
        return expr
    try:
        context_expr = parse_query(context_text)
    except QueryParseError:
        raise
    return and_expressions(context_expr, expr)
