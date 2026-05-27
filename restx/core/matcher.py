"""Endpoint matching engine for the query DSL."""

from __future__ import annotations

import fnmatch
import re
from typing import Iterable

from .dsl_parser import AndExpr, Condition, Expr, FieldKind, MatchMode, OrExpr
from .errors import QueryParseError
from .models import Endpoint, Parameter, ParsedSpec, SchemaNode

ZERO_MATCH_MESSAGE = "No matches. Try a broader search or wider glob."

SCORE_PATH_EXACT = 400
SCORE_PATH = 300
SCORE_OPERATION = 200
SCORE_PARAMETER = 100


def execute_query(query: str, spec: ParsedSpec) -> list[Endpoint]:
    """Parse and evaluate a query against a loaded spec."""
    from .dsl_parser import parse_query

    try:
        expr = parse_query(query)
    except QueryParseError:
        raise
    return filter_endpoints(expr, spec.endpoints, spec)


def endpoint_search_index(
    endpoint: Endpoint,
    spec: ParsedSpec,
) -> dict[str, tuple[str, ...]]:
    """Return searchable endpoint metadata grouped by match-priority tier."""
    return {
        "path": (endpoint.path,),
        "operation": tuple(_operation_search_strings(endpoint)),
        "parameters": tuple(_all_request_parameter_names(endpoint, spec)),
    }


def filter_endpoints(
    expr: Expr,
    endpoints: Iterable[Endpoint],
    spec: ParsedSpec,
) -> list[Endpoint]:
    matches = [endpoint for endpoint in endpoints if evaluate(expr, endpoint, spec)]
    if _expr_has_text(expr):
        matches.sort(
            key=lambda endpoint: (
                -_text_score_for_expr(expr, endpoint, spec),
                endpoint.path,
                endpoint.method,
            )
        )
    return matches


def evaluate(expr: Expr, endpoint: Endpoint, spec: ParsedSpec) -> bool:
    if isinstance(expr, AndExpr):
        return all(evaluate(term, endpoint, spec) for term in expr.terms)
    if isinstance(expr, OrExpr):
        return any(evaluate(term, endpoint, spec) for term in expr.terms)
    if isinstance(expr, Condition):
        return _evaluate_condition(expr, endpoint, spec)
    raise TypeError(f"Unknown expression type: {type(expr)!r}")


def _evaluate_condition(
    condition: Condition,
    endpoint: Endpoint,
    spec: ParsedSpec,
) -> bool:
    if condition.field == FieldKind.METHOD:
        return _match_method(condition, endpoint.method)
    if condition.field == FieldKind.PATH:
        return _match_path(condition, endpoint.path)
    if condition.field == FieldKind.TEXT:
        return _score_text_match(condition, endpoint, spec) > 0
    if condition.field == FieldKind.REQ:
        names = _input_parameter_names(endpoint)
        return _match_name_list(condition, names)
    if condition.field == FieldKind.REQPATH:
        names = _path_parameter_names(endpoint)
        return _match_name_list(condition, names)
    if condition.field == FieldKind.RESP:
        names = _response_field_names(endpoint, spec)
        return _match_name_list(condition, names)
    return False


def _expr_has_text(expr: Expr) -> bool:
    if isinstance(expr, Condition):
        return expr.field == FieldKind.TEXT
    if isinstance(expr, AndExpr):
        return any(_expr_has_text(term) for term in expr.terms)
    if isinstance(expr, OrExpr):
        return any(_expr_has_text(term) for term in expr.terms)
    return False


def _text_score_for_expr(expr: Expr, endpoint: Endpoint, spec: ParsedSpec) -> int:
    if isinstance(expr, Condition):
        if expr.field == FieldKind.TEXT:
            return _score_text_match(expr, endpoint, spec)
        return 0
    if isinstance(expr, AndExpr):
        if not _expr_has_text(expr):
            return 0
        scores = [
            _text_score_for_expr(term, endpoint, spec)
            for term in expr.terms
            if _expr_has_text(term)
        ]
        return max(scores) if scores else 0
    if isinstance(expr, OrExpr):
        return max(_text_score_for_expr(term, endpoint, spec) for term in expr.terms)
    return 0


def _has_glob_metacharacters(pattern: str) -> bool:
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "\\" and index + 1 < length:
            index += 2
            continue
        if char in "*?[":
            return True
        index += 1
    return False


def _text_matches_pattern(text: str, pattern: str, mode: MatchMode) -> bool:
    if mode == MatchMode.EXACT:
        return text == pattern
    if mode == MatchMode.NOT_EQUAL:
        return text != pattern
    if mode == MatchMode.REGEX:
        return bool(re.search(pattern, text, re.IGNORECASE))
    glob_pattern = pattern
    if not _has_glob_metacharacters(pattern):
        glob_pattern = f"*{pattern}*"
    return fnmatch.fnmatchcase(text.lower(), glob_pattern.lower())


def _match_text_in_strings(
    pattern: str,
    values: Iterable[str],
    mode: MatchMode,
) -> bool:
    return any(_text_matches_pattern(value, pattern, mode) for value in values)


def _operation_search_strings(endpoint: Endpoint) -> list[str]:
    values: list[str] = []
    if endpoint.operation_id:
        values.append(endpoint.operation_id)
    if endpoint.summary:
        values.append(endpoint.summary)
    return values


def _all_request_parameter_names(endpoint: Endpoint, spec: ParsedSpec) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    for parameter in endpoint.parameters:
        if parameter.name not in seen:
            seen.add(parameter.name)
            names.append(parameter.name)
        if parameter.schema is not None:
            for name in _collect_schema_field_names(parameter.schema, spec.schemas):
                if name not in seen:
                    seen.add(name)
                    names.append(name)

    return names


def _score_text_match(
    condition: Condition,
    endpoint: Endpoint,
    spec: ParsedSpec,
) -> int:
    pattern = condition.value
    mode = condition.mode

    if mode == MatchMode.EXACT and _text_matches_pattern(endpoint.path, pattern, mode):
        return SCORE_PATH_EXACT
    if _text_matches_pattern(endpoint.path, pattern, mode):
        return SCORE_PATH

    if _match_text_in_strings(pattern, _operation_search_strings(endpoint), mode):
        return SCORE_OPERATION

    if _match_text_in_strings(
        pattern,
        _all_request_parameter_names(endpoint, spec),
        mode,
    ):
        return SCORE_PARAMETER

    return 0


def _match_method(condition: Condition, method: str) -> bool:
    value = condition.value.upper()
    if condition.mode == MatchMode.EXACT:
        return method == value
    if condition.mode == MatchMode.NOT_EQUAL:
        return method != value.upper()
    if condition.mode == MatchMode.GLOB:
        return fnmatch.fnmatchcase(method, value.upper())
    if condition.mode == MatchMode.REGEX:
        return bool(re.search(condition.value, method, re.IGNORECASE))
    return False


def _match_path(condition: Condition, path: str) -> bool:
    pattern = condition.value
    if condition.mode == MatchMode.EXACT:
        return path == pattern
    if condition.mode == MatchMode.NOT_EQUAL:
        return path != pattern
    if condition.mode == MatchMode.REGEX:
        return bool(re.search(pattern, path))
    return path_glob_match(pattern, path)


def path_glob_match(pattern: str, path: str) -> bool:
    """Match a path template using glob rules; {param} segments are literal."""
    if "**" not in pattern:
        return fnmatch.fnmatchcase(path, pattern)

    segments = pattern.split("**")
    regex_parts = ["^"]
    for index, segment in enumerate(segments):
        if segment:
            regex_parts.append(_glob_segment_to_regex(segment))
        if index < len(segments) - 1:
            regex_parts.append(".*")
    regex_parts.append("$")
    return bool(re.fullmatch("".join(regex_parts), path))


def _glob_segment_to_regex(segment: str) -> str:
    """Convert a glob path segment (without **) to a regex fragment."""
    parts: list[str] = []
    index = 0
    length = len(segment)

    while index < length:
        char = segment[index]
        if char == "\\" and index + 1 < length:
            parts.append(re.escape(segment[index + 1]))
            index += 2
            continue
        if char == "*":
            parts.append("[^/]*")
            index += 1
            continue
        if char == "?":
            parts.append("[^/]")
            index += 1
            continue
        if char == "[":
            end = segment.find("]", index + 1)
            if end == -1:
                parts.append(re.escape(char))
                index += 1
                continue
            parts.append(re.escape(segment[index : end + 1]))
            index = end + 1
            continue
        parts.append(re.escape(char))
        index += 1

    return "".join(parts)


def _match_name_list(condition: Condition, names: Iterable[str]) -> bool:
    candidates = list(names)
    if condition.mode == MatchMode.EXACT:
        return condition.value in candidates
    if condition.mode == MatchMode.NOT_EQUAL:
        return condition.value not in candidates
    if condition.mode == MatchMode.REGEX:
        regex = re.compile(condition.value)
        return any(regex.search(name) for name in candidates)
    return any(
        fnmatch.fnmatchcase(name, condition.value) for name in candidates
    )


def _input_parameter_names(endpoint: Endpoint) -> list[str]:
    return [
        parameter.name
        for parameter in endpoint.parameters
        if parameter.location != "path"
    ]


def _path_parameter_names(endpoint: Endpoint) -> list[str]:
    return [
        parameter.name
        for parameter in endpoint.parameters
        if parameter.location == "path"
    ]


def _response_field_names(endpoint: Endpoint, spec: ParsedSpec) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    for response in endpoint.responses:
        if response.schema is None:
            continue
        for name in _collect_schema_field_names(response.schema, spec.schemas):
            if name not in seen:
                seen.add(name)
                names.append(name)

    return names


def _collect_schema_field_names(
    schema: dict,
    component_schemas: dict[str, dict],
    *,
    _visited_refs: frozenset[str] | None = None,
) -> list[str]:
    visited = _visited_refs or frozenset()
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or ref in visited:
            return []
        resolved = _resolve_ref(ref, component_schemas)
        if resolved is None:
            return []
        return _collect_schema_field_names(
            resolved,
            component_schemas,
            _visited_refs=visited | {ref},
        )

    names: list[str] = []

    properties = schema.get("properties")
    if isinstance(properties, dict):
        names.extend(str(key) for key in properties)
        for prop_schema in properties.values():
            if isinstance(prop_schema, dict):
                names.extend(
                    _collect_schema_field_names(
                        prop_schema,
                        component_schemas,
                        _visited_refs=visited,
                    )
                )

    items = schema.get("items")
    if isinstance(items, dict):
        names.extend(_collect_schema_field_names(items, component_schemas, _visited_refs=visited))

    for key in ("allOf", "anyOf", "oneOf"):
        group = schema.get(key)
        if isinstance(group, list):
            for item in group:
                if isinstance(item, dict):
                    names.extend(
                        _collect_schema_field_names(
                            item,
                            component_schemas,
                            _visited_refs=visited,
                        )
                    )

    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        names.extend(
            _collect_schema_field_names(
                additional,
                component_schemas,
                _visited_refs=visited,
            )
        )

    return names


def _resolve_ref(ref: str, component_schemas: dict[str, dict]) -> dict | None:
    prefix = "#/components/schemas/"
    if not ref.startswith(prefix):
        return None
    name = ref[len(prefix) :]
    schema = component_schemas.get(name)
    if isinstance(schema, dict):
        return schema
    return None


def summarize_schema_node(node: SchemaNode | None) -> str | None:
    """Return a short schema summary such as ``User object, 8 fields``."""
    if node is None:
        return None

    if node.type == "array":
        if len(node.children) == 1:
            return summarize_schema_node(node.children[0])
        if node.children:
            return f"array, {len(node.children)} items"
        return "array"

    if node.type == "object" or node.ref_name or node.children:
        type_name = node.ref_name or "object"
        label = f"{type_name} object" if node.ref_name or node.type == "object" else type_name
        field_count = len(node.children)
        if field_count:
            return f"{label}, {field_count} fields"
        return label

    return None


def schema_node_is_expandable(node: SchemaNode | None) -> bool:
    """Return whether a schema node can be interactively expanded."""
    if node is None:
        return False
    if node.type == "array":
        if len(node.children) == 1:
            return schema_node_is_expandable(node.children[0])
        return bool(node.children)
    return bool(node.children)


def format_schema_field_label(node: SchemaNode) -> str:
    """Format a schema field label for detail views (no expand indicator)."""
    name = node.name or "?"
    summary = summarize_schema_node(node)
    if summary and schema_node_is_expandable(node):
        return f"{name} ({summary})"

    type_parts = [node.type]
    if node.format:
        type_parts.append(node.format)
    required_label = "required" if node.required else "optional"
    return f"{name} ({', '.join(type_parts)}, {required_label})"


def _format_parameter(parameter: Parameter) -> str:
    if parameter.location == "path":
        text = f"{parameter.name} (path)"
    else:
        text = parameter.name

    summary = summarize_schema_node(parameter.schema_node)
    if summary:
        return f"{text} ({summary})"
    return text


def format_params(endpoint: Endpoint) -> str:
    """Format parameter names for match list output."""
    if not endpoint.parameters:
        return ""

    return ", ".join(_format_parameter(parameter) for parameter in endpoint.parameters)


def match_method_width(matches: list[Endpoint]) -> int:
    """Return the padded HTTP method column width for a result set."""
    if not matches:
        return 4
    return max(max(len(endpoint.method) for endpoint in matches), 4)


def match_header_segment(
    index: int,
    endpoint: Endpoint,
    *,
    method_width: int,
) -> str:
    """Return the ``[N] METHOD PATH`` segment without trailing padding."""
    return f"[{index}] {endpoint.method:<{method_width}} {endpoint.path}"


def match_header_width(
    matches: list[Endpoint],
    *,
    method_width: int | None = None,
) -> int:
    """Return the maximum width of the ``[N] METHOD PATH`` segment."""
    if not matches:
        return 0

    width = method_width if method_width is not None else match_method_width(matches)
    return max(
        len(match_header_segment(index, endpoint, method_width=width))
        for index, endpoint in enumerate(matches, start=1)
    )


def format_match_line(
    index: int,
    endpoint: Endpoint,
    *,
    header_width: int,
    method_width: int,
    indent: str = "  ",
) -> str:
    """Format a single aligned match line without terminal wrapping."""
    header = match_header_segment(index, endpoint, method_width=method_width)
    padded = header + " " * (header_width - len(header))
    params = format_params(endpoint)
    if params:
        return f"{indent}{padded} params: {params}"
    return f"{indent}{padded}"


def format_match_results(matches: list[Endpoint]) -> str:
    if not matches:
        return ZERO_MATCH_MESSAGE

    count = len(matches)
    label = "match" if count == 1 else "matches"
    method_width = match_method_width(matches)
    header_width = match_header_width(matches, method_width=method_width)
    lines = [f"  {count} {label}:"]
    for index, endpoint in enumerate(matches, start=1):
        lines.append(
            format_match_line(
                index,
                endpoint,
                header_width=header_width,
                method_width=method_width,
            )
        )
    return "\n".join(lines)
