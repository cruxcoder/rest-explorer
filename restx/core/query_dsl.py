"""Query DSL public API (parser + matcher)."""

from __future__ import annotations

from .dsl_parser import Condition, Expr, FieldKind, MatchMode, parse_query
from .matcher import (
    SCORE_OPERATION,
    SCORE_PARAMETER,
    SCORE_PATH,
    SCORE_PATH_EXACT,
    endpoint_search_index,
    execute_query,
    filter_endpoints,
    format_match_results,
)

__all__ = [
    "Condition",
    "Expr",
    "FieldKind",
    "MatchMode",
    "SCORE_OPERATION",
    "SCORE_PARAMETER",
    "SCORE_PATH",
    "SCORE_PATH_EXACT",
    "endpoint_search_index",
    "execute_query",
    "filter_endpoints",
    "format_match_results",
    "parse_query",
]
