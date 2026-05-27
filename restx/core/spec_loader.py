"""Spec loading and parsing for OpenAPI/Swagger specifications."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests
import yaml

from .errors import SpecLoadError, SpecParseError, UnsupportedSpecVersionError
from .models import Endpoint, Parameter, ParsedSpec, Response, SchemaNode, SecurityScheme

AUTH_TYPE_NONE = "None"
AUTH_TYPE_MIXED = "Mixed"
AUTH_TYPE_BEARER = "Bearer Token"
AUTH_TYPE_API_KEY = "API Key"
AUTH_TYPE_BASIC = "Basic Auth"

AUTH_DISPLAY_NAMES = {
    AUTH_TYPE_BEARER: "Bearer",
    AUTH_TYPE_API_KEY: "API Key",
    AUTH_TYPE_BASIC: "Basic",
}

SUPPORTED_VERSION_MESSAGE = (
    "RestX supports Swagger 2.0 and OpenAPI 3.0.x–3.2.x."
)

DEFAULT_FETCH_TIMEOUT = 30

SpinnerCallback = Callable[[str], None]


def _notify_spinner(spinner: SpinnerCallback | None, phase: str) -> None:
    if spinner is not None:
        spinner(phase)


def load_spec(
    source: str | None = None,
    *,
    spinner: SpinnerCallback | None = None,
) -> ParsedSpec:
    """Load a spec from URL, file path, or stdin based on the source argument."""
    if source is None:
        if sys.stdin.isatty():
            raise SpecLoadError(
                "No input received on stdin. Pipe a spec file: cat spec.json | restx"
            )
        return load_spec_from_stdin(spinner=spinner)

    if source.startswith(("http://", "https://")):
        return load_spec_from_url(source, spinner=spinner)

    return load_spec_from_file(source, spinner=spinner)


def load_spec_from_file(
    path: str,
    *,
    spinner: SpinnerCallback | None = None,
) -> ParsedSpec:
    """Load and parse an OpenAPI/Swagger spec from a local file path."""
    file_path = Path(path)

    if not file_path.exists():
        raise SpecLoadError(
            f"File not found: '{path}'. Check the path and try again."
        )

    if not file_path.is_file():
        raise SpecLoadError(
            f"File not found: '{path}'. Check the path and try again."
        )

    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecLoadError(
            f"File not found: '{path}'. Check the path and try again."
        ) from exc

    try:
        _notify_spinner(spinner, "parse")
        return parse_spec_text(raw_text, source_label=path)
    finally:
        _notify_spinner(spinner, "done")


def load_spec_from_url(
    url: str,
    *,
    spinner: SpinnerCallback | None = None,
) -> ParsedSpec:
    """Fetch and parse an OpenAPI/Swagger spec from a remote URL."""
    try:
        _notify_spinner(spinner, "fetch")
        try:
            response = requests.get(url, timeout=DEFAULT_FETCH_TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            raise SpecLoadError(
                f"Failed to fetch '{url}': Connection timed out. "
                "Verify the URL is accessible."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            reason = _connection_error_reason(exc)
            raise SpecLoadError(
                f"Failed to fetch '{url}': {reason}. Verify the URL is accessible."
            ) from exc
        except requests.exceptions.HTTPError as exc:
            status = (
                exc.response.status_code
                if exc.response is not None
                else "HTTP error"
            )
            raise SpecLoadError(
                f"Failed to fetch '{url}': HTTP {status}. "
                "Verify the URL is accessible."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise SpecLoadError(
                f"Failed to fetch '{url}': {exc}. Verify the URL is accessible."
            ) from exc

        _notify_spinner(spinner, "parse")
        return parse_spec_text(response.text, source_label=url)
    finally:
        _notify_spinner(spinner, "done")


def load_spec_from_stdin(
    *,
    spinner: SpinnerCallback | None = None,
) -> ParsedSpec:
    """Read and parse an OpenAPI/Swagger spec from stdin."""
    raw_text = sys.stdin.read()
    if not raw_text.strip():
        raise SpecLoadError(
            "No input received on stdin. Pipe a spec file: cat spec.json | restx"
        )
    try:
        _notify_spinner(spinner, "parse")
        return parse_spec_text(raw_text, source_label="stdin")
    finally:
        _notify_spinner(spinner, "done")


def parse_spec_text(raw_text: str, source_label: str = "<input>") -> ParsedSpec:
    """Parse spec text (JSON or YAML) into the internal model."""
    if not raw_text.strip():
        raise SpecParseError(
            f"Failed to parse spec: empty input from '{source_label}'. "
            "Verify the file contains valid JSON or YAML."
        )

    document = _load_document(raw_text, source_label)
    return parse_spec_document(document)


def parse_spec_document(document: dict) -> ParsedSpec:
    """Parse a loaded spec document dict into the internal model."""
    version_kind = _validate_spec_version(document)
    if version_kind == "swagger_20":
        return _parse_swagger_20(document)
    return _parse_openapi_3x(document)


def classify_security_scheme(scheme: SecurityScheme) -> str | None:
    """Map a parsed security scheme to a display auth type label."""
    scheme_type = scheme.type.lower()
    if scheme_type == "apikey":
        return AUTH_TYPE_API_KEY
    if scheme_type == "http":
        http_scheme = (scheme.scheme or "").lower()
        if http_scheme == "bearer":
            return AUTH_TYPE_BEARER
        if http_scheme == "basic":
            return AUTH_TYPE_BASIC
    if scheme_type in {"oauth2", "openidconnect"}:
        return AUTH_TYPE_BEARER
    return None


def collect_auth_types(spec: ParsedSpec) -> set[str]:
    """Collect recognized auth type labels declared in the spec."""
    if not spec.security_schemes:
        return set()

    auth_types: set[str] = set()
    for scheme in spec.security_schemes.values():
        auth_type = classify_security_scheme(scheme)
        if auth_type is not None:
            auth_types.add(auth_type)
    return auth_types


def detect_auth_type(spec: ParsedSpec) -> str:
    """Detect the primary authentication type for a loaded spec."""
    auth_types = collect_auth_types(spec)
    if not auth_types:
        return AUTH_TYPE_NONE
    if len(auth_types) == 1:
        return next(iter(auth_types))
    return AUTH_TYPE_MIXED


def format_auth_display(spec: ParsedSpec) -> str:
    """Format auth type for the loaded-spec summary line."""
    auth_types = collect_auth_types(spec)
    if not auth_types:
        return AUTH_TYPE_NONE
    if len(auth_types) == 1:
        auth_type = next(iter(auth_types))
        return AUTH_DISPLAY_NAMES.get(auth_type, auth_type)
    labels = sorted(
        AUTH_DISPLAY_NAMES.get(auth_type, auth_type) for auth_type in auth_types
    )
    return f"Mixed ({', '.join(labels)})"


def format_loaded_spec_message(spec: ParsedSpec) -> str:
    """Format the one-line summary shown after a spec is loaded."""
    path_count = len({endpoint.path for endpoint in spec.endpoints})
    server_count = len(spec.servers)
    server_label = "1 server" if server_count == 1 else f"{server_count} servers"
    auth_type = format_auth_display(spec)
    return (
        f"Loaded spec: {spec.title} v{spec.version} ({spec.openapi_version}) — "
        f"{spec.endpoint_count} endpoints, {path_count} paths, {server_label}, "
        f"Auth: {auth_type}"
    )


def _connection_error_reason(exc: requests.exceptions.ConnectionError) -> str:
    message = str(exc.args[0]) if exc.args else str(exc)
    lowered = message.lower()
    if "name or service not known" in lowered or "nodename nor servname" in lowered:
        return "DNS lookup failed"
    if "connection refused" in lowered:
        return "Connection refused"
    return message.split(":", 1)[0] if message else "Connection error"


def _load_document(raw_text: str, source_label: str) -> dict:
    stripped = raw_text.lstrip()
    if stripped.startswith(("{", "[")):
        return _parse_json_document(raw_text, source_label)
    return _parse_yaml_document(raw_text, source_label)


def _parse_json_document(raw_text: str, source_label: str) -> dict:
    try:
        document = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SpecParseError(
            "Failed to parse spec: JSON error at line "
            f"{exc.lineno} — {exc.msg}. Verify the file is valid JSON."
        ) from exc

    if not isinstance(document, dict):
        raise SpecParseError(
            f"Failed to parse spec: expected a JSON object in '{source_label}'. "
            "Verify the file is valid OpenAPI JSON."
        )
    return document


def _parse_yaml_document(raw_text: str, source_label: str) -> dict:
    try:
        document = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise SpecParseError(_format_yaml_error(exc)) from exc

    if document is None:
        raise SpecParseError(
            f"Failed to parse spec: empty YAML document in '{source_label}'. "
            "Verify the file is valid YAML."
        )

    if not isinstance(document, dict):
        raise SpecParseError(
            f"Failed to parse spec: expected a YAML mapping in '{source_label}'. "
            "Verify the file is valid OpenAPI YAML."
        )
    return document


def _format_yaml_error(exc: yaml.YAMLError) -> str:
    problem = getattr(exc, "problem", None) or str(exc)
    mark = getattr(exc, "problem_mark", None)
    if mark is not None:
        line = mark.line + 1
        return (
            f"Failed to parse spec: YAML error at line {line} — {problem}. "
            "Verify the file is valid YAML."
        )
    return (
        f"Failed to parse spec: YAML error — {problem}. "
        "Verify the file is valid YAML."
    )


def _validate_spec_version(document: dict) -> str:
    if "swagger" in document:
        version = str(document["swagger"])
        major_minor = _normalize_version(version)
        if major_minor.startswith("1."):
            raise UnsupportedSpecVersionError(
                f"Unsupported spec version: {major_minor}. {SUPPORTED_VERSION_MESSAGE}"
            )
        if major_minor == "2.0":
            return "swagger_20"
        raise UnsupportedSpecVersionError(
            f"Unsupported spec version: {major_minor}. {SUPPORTED_VERSION_MESSAGE}"
        )

    openapi_version = document.get("openapi")
    if not openapi_version:
        raise SpecParseError(
            "Failed to parse spec: missing 'openapi' or 'swagger' version field. "
            "Verify the file is a valid OpenAPI or Swagger specification."
        )

    version_text = str(openapi_version).strip()
    if not _is_supported_openapi_version(version_text):
        raise UnsupportedSpecVersionError(
            f"Unsupported spec version: {_normalize_version(version_text)}. "
            f"{SUPPORTED_VERSION_MESSAGE}"
        )
    return "openapi_3x"


def _normalize_version(version: str) -> str:
    parts = version.strip().split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return version.strip()


def _is_supported_openapi_version(version: str) -> bool:
    return (
        version.startswith("3.0.")
        or version.startswith("3.1.")
        or version.startswith("3.2.")
    )


def _parse_swagger_20(document: dict) -> ParsedSpec:
    info = document.get("info", {})
    title = str(info.get("title", "Untitled"))
    version = str(info.get("version", "unknown"))

    servers = _parse_swagger_20_servers(document)
    security_schemes = _parse_swagger_20_security_schemes(document)
    schemas = _parse_swagger_20_definitions(document)
    global_security = document.get("security", []) or []

    endpoints: list[Endpoint] = []
    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        raise SpecParseError(
            "Failed to parse spec: 'paths' must be an object. "
            "Verify the file is valid Swagger 2.0."
        )

    http_methods = (
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "options",
    )

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        shared_parameters = _parse_swagger_20_parameters(
            path_item.get("parameters", []), schemas
        )

        for method in http_methods:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue

            operation_parameters = _parse_swagger_20_parameters(
                operation.get("parameters", []), schemas
            )
            parameters = _merge_parameters(shared_parameters, operation_parameters)
            responses = _parse_swagger_20_responses(
                operation.get("responses", {}), schemas
            )
            security = operation.get("security", global_security) or []

            endpoints.append(
                Endpoint(
                    method=method.upper(),
                    path=str(path),
                    operation_id=operation.get("operationId"),
                    summary=operation.get("summary"),
                    parameters=parameters,
                    responses=responses,
                    security=security,
                )
            )

    return ParsedSpec(
        title=title,
        version=version,
        openapi_version="2.0",
        servers=servers,
        endpoints=endpoints,
        security_schemes=security_schemes,
        global_security=global_security,
        schemas=schemas,
    )


def _parse_swagger_20_servers(document: dict) -> list[str]:
    host = str(document.get("host", "")).strip()
    base_path = str(document.get("basePath", "")).strip()
    schemes = document.get("schemes") or ["https"]
    if not isinstance(schemes, list):
        schemes = ["https"]

    if not host:
        return []

    servers: list[str] = []
    for scheme in schemes:
        servers.append(f"{scheme}://{host}{base_path}")
    return servers


def _parse_swagger_20_definitions(document: dict) -> dict[str, dict]:
    definitions = document.get("definitions", {})
    if not isinstance(definitions, dict):
        return {}

    return {
        str(name): schema
        for name, schema in definitions.items()
        if isinstance(schema, dict)
    }


def _parse_swagger_20_security_schemes(document: dict) -> dict[str, SecurityScheme]:
    raw_schemes = document.get("securityDefinitions", {})
    if not isinstance(raw_schemes, dict):
        return {}

    schemes: dict[str, SecurityScheme] = {}
    for name, scheme in raw_schemes.items():
        if not isinstance(scheme, dict):
            continue
        schemes[name] = SecurityScheme(
            name=str(name),
            type=str(scheme.get("type", "")),
            description=scheme.get("description"),
            scheme=scheme.get("scheme"),
            bearer_format=None,
            in_=scheme.get("in"),
            param_name=scheme.get("name"),
        )
    return schemes


def _parse_swagger_20_parameters(
    raw_parameters: object,
    schemas: dict[str, dict],
) -> list[Parameter]:
    if not isinstance(raw_parameters, list):
        return []

    parameters: list[Parameter] = []
    for item in raw_parameters:
        if not isinstance(item, dict):
            continue
        if "$ref" in item:
            continue

        location = item.get("in")
        if location == "body":
            schema = item.get("schema")
            if schema is None:
                schema = _swagger_20_type_to_schema(item)
            schema_dict = schema if isinstance(schema, dict) else None
            parameters.append(
                Parameter(
                    name=str(item.get("name", "body")),
                    location="body",
                    required=bool(item.get("required", False)),
                    schema=schema_dict,
                    schema_node=_parse_schema_node(
                        schema_dict,
                        schemas,
                        name=str(item.get("name", "body")),
                        required=bool(item.get("required", False)),
                    ),
                    description=item.get("description"),
                )
            )
            continue

        name = item.get("name")
        if not name or not location:
            continue

        schema_dict = _swagger_20_type_to_schema(item)
        parameters.append(
            Parameter(
                name=str(name),
                location=str(location),
                required=bool(item.get("required", False)),
                schema=schema_dict,
                schema_node=_parse_schema_node(
                    schema_dict,
                    schemas,
                    name=str(name),
                    required=bool(item.get("required", False)),
                ),
                description=item.get("description"),
            )
        )
    return parameters


def _swagger_20_type_to_schema(item: dict) -> dict[str, Any] | None:
    schema_type = item.get("type")
    if not schema_type:
        return None

    schema: dict[str, Any] = {"type": schema_type}
    for field in ("format", "enum", "items", "default", "example"):
        if field in item:
            schema[field] = item[field]
    return schema


def _parse_swagger_20_responses(
    raw_responses: object,
    schemas: dict[str, dict],
) -> list[Response]:
    if not isinstance(raw_responses, dict):
        return []

    responses: list[Response] = []
    for status_code, response in raw_responses.items():
        if not isinstance(response, dict):
            continue

        schema = response.get("schema")
        schema_dict = schema if isinstance(schema, dict) else None
        responses.append(
            Response(
                status_code=str(status_code),
                description=str(response.get("description", "")),
                schema=schema_dict,
                schema_node=_parse_schema_node(schema_dict, schemas),
                content=None,
            )
        )
    return responses


def _parse_openapi_3x(document: dict) -> ParsedSpec:
    info = document.get("info", {})
    title = str(info.get("title", "Untitled"))
    version = str(info.get("version", "unknown"))
    openapi_version = str(document.get("openapi", "unknown"))

    servers = [
        str(server.get("url", ""))
        for server in document.get("servers", [])
        if isinstance(server, dict)
    ]

    security_schemes = _parse_security_schemes(document)
    schemas = _parse_component_schemas(document)
    global_security = document.get("security", []) or []

    endpoints: list[Endpoint] = []
    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        raise SpecParseError(
            "Failed to parse spec: 'paths' must be an object. "
            "Verify the file is valid OpenAPI."
        )

    http_methods = (
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "options",
        "trace",
    )

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        shared_parameters = _parse_parameters(path_item.get("parameters", []), schemas)

        for method in http_methods:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue

            operation_parameters = _parse_parameters(
                operation.get("parameters", []), schemas
            )
            request_body_parameters = _parse_request_body_parameters(
                operation.get("requestBody"), schemas
            )

            parameters = _merge_parameters(
                shared_parameters,
                operation_parameters,
                request_body_parameters,
            )

            responses = _parse_responses(operation.get("responses", {}), schemas)
            security = operation.get("security", global_security) or []

            endpoints.append(
                Endpoint(
                    method=method.upper(),
                    path=str(path),
                    operation_id=operation.get("operationId"),
                    summary=operation.get("summary"),
                    parameters=parameters,
                    responses=responses,
                    security=security,
                )
            )

    return ParsedSpec(
        title=title,
        version=version,
        openapi_version=openapi_version,
        servers=servers,
        endpoints=endpoints,
        security_schemes=security_schemes,
        global_security=global_security,
        schemas=schemas,
    )


def _parse_component_schemas(document: dict) -> dict[str, dict]:
    components = document.get("components", {})
    if not isinstance(components, dict):
        return {}

    raw_schemas = components.get("schemas", {})
    if not isinstance(raw_schemas, dict):
        return {}

    return {
        str(name): schema
        for name, schema in raw_schemas.items()
        if isinstance(schema, dict)
    }


def _parse_security_schemes(document: dict) -> dict[str, SecurityScheme]:
    components = document.get("components", {})
    if not isinstance(components, dict):
        return {}

    raw_schemes = components.get("securitySchemes", {})
    if not isinstance(raw_schemes, dict):
        return {}

    schemes: dict[str, SecurityScheme] = {}
    for name, scheme in raw_schemes.items():
        if not isinstance(scheme, dict):
            continue
        schemes[name] = SecurityScheme(
            name=str(name),
            type=str(scheme.get("type", "")),
            description=scheme.get("description"),
            scheme=scheme.get("scheme"),
            bearer_format=scheme.get("bearerFormat"),
            in_=scheme.get("in"),
            param_name=scheme.get("name"),
        )
    return schemes


def _parse_parameters(
    raw_parameters: object,
    schemas: dict[str, dict],
) -> list[Parameter]:
    if not isinstance(raw_parameters, list):
        return []

    parameters: list[Parameter] = []
    for item in raw_parameters:
        if not isinstance(item, dict):
            continue
        if "$ref" in item:
            continue

        name = item.get("name")
        location = item.get("in")
        if not name or not location:
            continue

        schema_dict = item.get("schema")
        schema_dict = schema_dict if isinstance(schema_dict, dict) else None
        parameters.append(
            Parameter(
                name=str(name),
                location=str(location),
                required=bool(item.get("required", False)),
                schema=schema_dict,
                schema_node=_parse_schema_node(
                    schema_dict,
                    schemas,
                    name=str(name),
                    required=bool(item.get("required", False)),
                ),
                description=item.get("description"),
            )
        )
    return parameters


def _parse_request_body_parameters(
    request_body: object,
    schemas: dict[str, dict],
) -> list[Parameter]:
    if not isinstance(request_body, dict):
        return []

    required = bool(request_body.get("required", False))
    schema_dict = _extract_content_schema(request_body.get("content"))

    return [
        Parameter(
            name="body",
            location="body",
            required=required,
            schema=schema_dict,
            schema_node=_parse_schema_node(
                schema_dict,
                schemas,
                name="body",
                required=required,
            ),
            description=request_body.get("description"),
        )
    ]


def _merge_parameters(*groups: list[Parameter]) -> list[Parameter]:
    merged: list[Parameter] = []
    seen: set[tuple[str, str]] = set()

    for group in groups:
        for parameter in group:
            key = (parameter.name, parameter.location)
            if key in seen:
                continue
            seen.add(key)
            merged.append(parameter)

    return merged


def _parse_responses(
    raw_responses: object,
    schemas: dict[str, dict],
) -> list[Response]:
    if not isinstance(raw_responses, dict):
        return []

    responses: list[Response] = []
    for status_code, response in raw_responses.items():
        if not isinstance(response, dict):
            continue

        content = response.get("content")
        schema_dict = _extract_content_schema(content)

        responses.append(
            Response(
                status_code=str(status_code),
                description=str(response.get("description", "")),
                schema=schema_dict,
                schema_node=_parse_schema_node(schema_dict, schemas),
                content=content if isinstance(content, dict) else None,
            )
        )

    return responses


def _extract_content_schema(content: object) -> dict | None:
    if not isinstance(content, dict):
        return None

    for media_type in content.values():
        if isinstance(media_type, dict) and "schema" in media_type:
            schema = media_type.get("schema")
            if isinstance(schema, dict):
                return schema
    return None


def _resolve_schema_ref(ref: str, schemas: dict[str, dict]) -> tuple[str, dict] | None:
    if not isinstance(ref, str):
        return None

    prefixes = ("#/components/schemas/", "#/definitions/")
    for prefix in prefixes:
        if ref.startswith(prefix):
            name = ref[len(prefix) :]
            schema = schemas.get(name)
            if isinstance(schema, dict):
                return name, schema
    return None


def _parse_schema_node(
    schema: dict | None,
    schemas: dict[str, dict],
    *,
    name: str | None = None,
    required: bool = False,
    visited_refs: frozenset[str] | None = None,
) -> SchemaNode | None:
    if not isinstance(schema, dict):
        return None

    visited = visited_refs or frozenset()
    ref_name: str | None = None

    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or ref in visited:
            return None
        resolved = _resolve_schema_ref(ref, schemas)
        if resolved is None:
            return SchemaNode(
                name=name,
                type="object",
                required=required,
                ref_name=ref.rsplit("/", 1)[-1],
                description=schema.get("description"),
            )
        ref_name, resolved_schema = resolved
        node = _parse_schema_node(
            resolved_schema,
            schemas,
            name=name,
            required=required,
            visited_refs=visited | {ref},
        )
        if node is None:
            return SchemaNode(
                name=name,
                type="object",
                required=required,
                ref_name=ref_name,
            )
        if node.ref_name is None:
            return SchemaNode(
                name=node.name,
                type=node.type,
                format=node.format,
                required=node.required,
                children=node.children,
                description=node.description or schema.get("description"),
                ref_name=ref_name,
            )
        return node

    for key in ("allOf", "anyOf", "oneOf"):
        group = schema.get(key)
        if isinstance(group, list) and group:
            return _parse_composition_schema(
                group,
                schemas,
                name=name,
                required=required,
                visited=visited,
                composition=key,
            )

    schema_type = str(schema.get("type", "object"))
    description = schema.get("description")
    schema_format = schema.get("format")
    if isinstance(schema_format, str):
        format_value: str | None = schema_format
    else:
        format_value = None

    if schema_type == "array":
        items = schema.get("items")
        item_node = _parse_schema_node(
            items if isinstance(items, dict) else None,
            schemas,
            visited_refs=visited,
        )
        children = [item_node] if item_node is not None else []
        return SchemaNode(
            name=name,
            type="array",
            format=format_value,
            required=required,
            children=children,
            description=description,
            ref_name=ref_name,
        )

    if schema_type == "object" or "properties" in schema:
        return _parse_object_schema(
            schema,
            schemas,
            name=name,
            required=required,
            visited=visited,
            ref_name=ref_name,
        )

    return SchemaNode(
        name=name,
        type=schema_type,
        format=format_value,
        required=required,
        description=description,
        ref_name=ref_name,
    )


def _parse_composition_schema(
    group: list,
    schemas: dict[str, dict],
    *,
    name: str | None,
    required: bool,
    visited: frozenset[str],
    composition: str,
) -> SchemaNode | None:
    merged_properties: dict[str, dict] = {}
    merged_required: set[str] = set()
    description: str | None = None
    ref_name: str | None = None
    schema_type = "object"

    for item in group:
        if not isinstance(item, dict):
            continue
        if "$ref" in item:
            ref = item["$ref"]
            if isinstance(ref, str):
                resolved = _resolve_schema_ref(ref, schemas)
                if resolved is not None:
                    ref_name = ref_name or resolved[0]
                    item = resolved[1]
                else:
                    continue
        if not isinstance(item, dict):
            continue

        if item.get("description") and description is None:
            description = item.get("description")

        item_type = item.get("type")
        if isinstance(item_type, str):
            schema_type = item_type

        properties = item.get("properties")
        if isinstance(properties, dict):
            merged_properties.update(properties)

        item_required = item.get("required")
        if isinstance(item_required, list):
            merged_required.update(str(field) for field in item_required)

    if composition == "oneOf" and len(group) == 1:
        return _parse_schema_node(
            group[0] if isinstance(group[0], dict) else None,
            schemas,
            name=name,
            required=required,
            visited_refs=visited,
        )

    if merged_properties:
        synthetic = {
            "type": schema_type,
            "properties": merged_properties,
            "required": sorted(merged_required),
            "description": description,
        }
        return _parse_object_schema(
            synthetic,
            schemas,
            name=name,
            required=required,
            visited=visited,
            ref_name=ref_name,
        )

    first = group[0] if group else None
    return _parse_schema_node(
        first if isinstance(first, dict) else None,
        schemas,
        name=name,
        required=required,
        visited_refs=visited,
    )


def _parse_object_schema(
    schema: dict,
    schemas: dict[str, dict],
    *,
    name: str | None,
    required: bool,
    visited: frozenset[str],
    ref_name: str | None = None,
) -> SchemaNode:
    properties = schema.get("properties")
    required_fields: set[str] = set()
    raw_required = schema.get("required")
    if isinstance(raw_required, list):
        required_fields = {str(field) for field in raw_required}

    children: list[SchemaNode] = []
    if isinstance(properties, dict):
        for prop_name, prop_schema in properties.items():
            if not isinstance(prop_schema, dict):
                continue
            child = _parse_schema_node(
                prop_schema,
                schemas,
                name=str(prop_name),
                required=str(prop_name) in required_fields,
                visited_refs=visited,
            )
            if child is not None:
                children.append(child)

    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        extra = _parse_schema_node(
            additional,
            schemas,
            name="[additionalProperties]",
            visited_refs=visited,
        )
        if extra is not None:
            children.append(extra)

    schema_type = str(schema.get("type", "object"))
    schema_format = schema.get("format")
    format_value = schema_format if isinstance(schema_format, str) else None

    return SchemaNode(
        name=name,
        type=schema_type,
        format=format_value,
        required=required,
        children=children,
        description=schema.get("description"),
        ref_name=ref_name,
    )
