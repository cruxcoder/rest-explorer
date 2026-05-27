"""Internal data models for parsed API specifications."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SchemaNode:
    """A node in a parsed request or response schema tree."""

    name: str | None = None
    type: str = "object"
    format: str | None = None
    required: bool = False
    children: list[SchemaNode] = field(default_factory=list)
    description: str | None = None
    ref_name: str | None = None


@dataclass(frozen=True)
class Parameter:
    name: str
    location: str
    required: bool = False
    schema: dict | None = None
    schema_node: SchemaNode | None = None
    description: str | None = None


@dataclass(frozen=True)
class Response:
    status_code: str
    description: str = ""
    schema: dict | None = None
    schema_node: SchemaNode | None = None
    content: dict | None = None


@dataclass(frozen=True)
class SecurityScheme:
    name: str
    type: str
    description: str | None = None
    scheme: str | None = None
    bearer_format: str | None = None
    in_: str | None = None
    param_name: str | None = None


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    operation_id: str | None = None
    summary: str | None = None
    parameters: list[Parameter] = field(default_factory=list)
    responses: list[Response] = field(default_factory=list)
    security: list[dict[str, list[str]]] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedSpec:
    title: str
    version: str
    openapi_version: str
    servers: list[str] = field(default_factory=list)
    endpoints: list[Endpoint] = field(default_factory=list)
    security_schemes: dict[str, SecurityScheme] = field(default_factory=dict)
    global_security: list[dict[str, list[str]]] = field(default_factory=list)
    schemas: dict[str, dict] = field(default_factory=dict)

    @property
    def endpoint_count(self) -> int:
        return len(self.endpoints)
