"""Generate curl boilerplate commands for API endpoints."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .matcher import path_glob_match
from .models import Endpoint, Parameter, ParsedSpec, SchemaNode, SecurityScheme

DEFAULT_BASE_URL = "https://api.example.com"

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})

_TYPE_PLACEHOLDERS = {
    "string": "<string>",
    "integer": "<integer>",
    "boolean": "<boolean>",
    "number": "<number>",
}


@dataclass(frozen=True)
class EndpointMatchResult:
    """Result of resolving an endpoint pattern against a loaded spec."""

    endpoints: tuple[Endpoint, ...]

    @property
    def is_unique(self) -> bool:
        return len(self.endpoints) == 1

    @property
    def is_ambiguous(self) -> bool:
        return len(self.endpoints) > 1

    @property
    def is_empty(self) -> bool:
        return len(self.endpoints) == 0


def resolve_endpoint(pattern: str, spec: ParsedSpec) -> list[Endpoint]:
    """Find endpoints matching a path glob or regex pattern."""
    method, path_pattern = _parse_endpoint_pattern(pattern)
    matches = [
        endpoint
        for endpoint in spec.endpoints
        if _endpoint_matches_pattern(endpoint, method, path_pattern)
    ]
    matches.sort(key=lambda endpoint: (endpoint.path, endpoint.method))
    return matches


def resolve_endpoint_match(pattern: str, spec: ParsedSpec) -> EndpointMatchResult:
    """Resolve a pattern and wrap the result for single/multi-match handling."""
    return EndpointMatchResult(tuple(resolve_endpoint(pattern, spec)))


def format_endpoint_choices(endpoints: list[Endpoint] | tuple[Endpoint, ...]) -> list[str]:
    """Prepare numbered endpoint labels for interactive selection."""
    return [
        f"{index}. {endpoint.method} {endpoint.path}"
        for index, endpoint in enumerate(endpoints, start=1)
    ]


def generate_curl(endpoint: Endpoint, spec: ParsedSpec) -> str:
    """Build a ready-to-use curl command for an endpoint."""
    base_url = _base_url(spec)
    url = _build_url(base_url, endpoint)
    headers = _build_headers(endpoint, spec)

    lines = [f"curl -X {endpoint.method} '{url}' \\"]
    for header in headers:
        lines.append(f"  -H '{header}' \\")

    body_line = _build_body_line(endpoint)
    if body_line is not None:
        lines.append(body_line)
    elif lines[-1].endswith(" \\"):
        lines[-1] = lines[-1][:-2]

    return "\n".join(lines)


def _parse_endpoint_pattern(pattern: str) -> tuple[str | None, str]:
    stripped = pattern.strip()
    if not stripped:
        return None, stripped

    parts = stripped.split(None, 1)
    if len(parts) == 2 and parts[0].upper() in _HTTP_METHODS:
        return parts[0].upper(), parts[1]
    return None, stripped


def _pattern_is_regex(pattern: str) -> bool:
    regex_markers = set(".^$+|(")
    return any(char in pattern for char in regex_markers) or "\\" in pattern


def _path_matches_pattern(path: str, pattern: str) -> bool:
    if _pattern_is_regex(pattern):
        try:
            return bool(re.search(pattern, path))
        except re.error:
            return False
    return path_glob_match(pattern, path)


def _endpoint_matches_pattern(
    endpoint: Endpoint,
    method: str | None,
    path_pattern: str,
) -> bool:
    if method is not None and endpoint.method != method:
        return False
    if not path_pattern:
        return True
    return _path_matches_pattern(endpoint.path, path_pattern)


def _base_url(spec: ParsedSpec) -> str:
    if spec.servers:
        return spec.servers[0].rstrip("/")
    return DEFAULT_BASE_URL


def _build_url(base_url: str, endpoint: Endpoint) -> str:
    path = endpoint.path
    query_params = [
        parameter.name
        for parameter in endpoint.parameters
        if parameter.location == "query"
    ]

    url = f"{base_url}{path}"
    if not query_params:
        return url

    query_string = "&".join(f"{name}={{{name}}}" for name in query_params)
    return f"{url}?{query_string}"


def _build_headers(endpoint: Endpoint, spec: ParsedSpec) -> list[str]:
    headers = ["Accept: application/json"]

    if _has_request_body(endpoint):
        headers.append("Content-Type: application/json")

    for scheme in _resolve_security_schemes(endpoint, spec):
        header = _security_header(scheme)
        if header is not None:
            headers.append(header)

    return headers


def _has_request_body(endpoint: Endpoint) -> bool:
    return _body_parameter(endpoint) is not None


def _body_parameter(endpoint: Endpoint) -> Parameter | None:
    for parameter in endpoint.parameters:
        if parameter.location == "body":
            return parameter
    return None


def _build_body_line(endpoint: Endpoint) -> str | None:
    body_param = _body_parameter(endpoint)
    if body_param is None:
        return None

    schema_node = body_param.schema_node
    if schema_node is None:
        return "  -d '{}'"

    template = _schema_to_template(schema_node)
    return _format_body_flag(template)


def _schema_to_template(node: SchemaNode) -> object:
    if node.type == "array":
        if node.children:
            return [_schema_to_template(node.children[0])]
        return []

    if node.type == "object" or node.children:
        properties: dict[str, object] = {}
        for child in node.children:
            if child.name is None or child.name == "[additionalProperties]":
                continue
            properties[child.name] = _schema_to_template(child)
        return properties

    return _type_placeholder(node.type)


def _type_placeholder(schema_type: str) -> str:
    return _TYPE_PLACEHOLDERS.get(schema_type, f"<{schema_type}>")


def _format_body_flag(template: object) -> str:
    json_body = json.dumps(template, indent=2)
    lines = json_body.splitlines()
    if len(lines) == 1:
        return f"  -d '{json_body}'"

    parts = [f"  -d '{lines[0]}"]
    for line in lines[1:-1]:
        parts.append(f"    {line}")
    parts.append(f"    {lines[-1]}'")
    return "\n".join(parts)


def _resolve_security_schemes(
    endpoint: Endpoint,
    spec: ParsedSpec,
) -> list[SecurityScheme]:
    requirements = endpoint.security
    if not requirements:
        return []

    schemes: list[SecurityScheme] = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        for scheme_name in requirement:
            scheme = spec.security_schemes.get(scheme_name)
            if scheme is not None:
                schemes.append(scheme)
                return schemes
    return schemes


def _security_header(scheme: SecurityScheme) -> str | None:
    if scheme.type == "http":
        if scheme.scheme == "bearer":
            return "Authorization: Bearer <YOUR_TOKEN_HERE>"
        if scheme.scheme == "basic":
            return "Authorization: Basic <USER:PASS_HERE>"
        return None

    if scheme.type == "apiKey" and scheme.in_ == "header":
        header_name = scheme.param_name or "X-API-Key"
        return f"{header_name}: <YOUR_API_KEY_HERE>"

    if scheme.type == "oauth2":
        return "Authorization: Bearer <YOUR_OAUTH_TOKEN_HERE>"

    return None
