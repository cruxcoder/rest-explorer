"""
RestX Core Module

Business logic for spec parsing, query evaluation, matching, and curl generation.
This layer must remain independent of terminal/UI libraries.
"""

from .context import MatchResultState, QueryContext, and_expressions, parse_combined_query
from .curl_generator import (
    EndpointMatchResult,
    format_endpoint_choices,
    generate_curl,
    resolve_endpoint,
    resolve_endpoint_match,
)
from .dsl_parser import Condition, Expr, MatchMode, parse_query
from .errors import (
    QueryParseError,
    RestXCoreError,
    SpecLoadError,
    SpecParseError,
    UnsupportedSpecVersionError,
)
from .matcher import execute_query, filter_endpoints, format_match_results
from .models import Endpoint, Parameter, ParsedSpec, Response, SchemaNode, SecurityScheme
from .pager import Pager
from .shell import launch_interactive_shell, run_shell_command
from .spec_loader import (
    detect_auth_type,
    format_loaded_spec_message,
    load_spec,
    load_spec_from_file,
    load_spec_from_stdin,
    load_spec_from_url,
    parse_spec_document,
    parse_spec_text,
)

__all__ = [
    "Condition",
    "Endpoint",
    "EndpointMatchResult",
    "Expr",
    "MatchMode",
    "Parameter",
    "Pager",
    "ParsedSpec",
    "MatchResultState",
    "QueryContext",
    "QueryParseError",
    "Response",
    "RestXCoreError",
    "SchemaNode",
    "SecurityScheme",
    "SpecLoadError",
    "SpecParseError",
    "UnsupportedSpecVersionError",
    "and_expressions",
    "detect_auth_type",
    "execute_query",
    "format_loaded_spec_message",
    "filter_endpoints",
    "format_endpoint_choices",
    "format_match_results",
    "generate_curl",
    "launch_interactive_shell",
    "load_spec",
    "load_spec_from_file",
    "load_spec_from_stdin",
    "load_spec_from_url",
    "parse_combined_query",
    "parse_query",
    "parse_spec_document",
    "parse_spec_text",
    "resolve_endpoint",
    "resolve_endpoint_match",
    "run_shell_command",
]
