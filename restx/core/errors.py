"""Core-layer exceptions for spec loading and parsing."""


class RestXCoreError(Exception):
    """Base exception for RestX core errors."""


class SpecLoadError(RestXCoreError):
    """Raised when a spec cannot be loaded from its source."""


class SpecParseError(RestXCoreError):
    """Raised when spec content is malformed or invalid."""


class UnsupportedSpecVersionError(SpecParseError):
    """Raised when the spec version is not supported."""


class QueryParseError(RestXCoreError):
    """Raised when a DSL query string is syntactically invalid."""

    def __init__(self, message: str, suggestion: str | None = None) -> None:
        super().__init__(message)
        self.suggestion = suggestion
