"""Context-aware tab completion for the RestX REPL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from restx.cli.commands import DOT_REPL_COMMANDS
from restx.core.context import QueryContext
from restx.core.dsl_parser import HTTP_METHODS, parse_query
from restx.core.errors import QueryParseError
from restx.core.matcher import filter_endpoints
from restx.core.models import Endpoint, ParsedSpec

HTTP_METHOD_CHOICES = ("GET", "POST", "PUT", "DELETE", "PATCH")
FIELD_PREFIXES = ("req:", "resp:", "reqpath:")
REPL_COMMANDS = DOT_REPL_COMMANDS
_META_COMMAND_TYPING_HINTS = (
    "help",
    "?",
    "status",
    "clear",
    "quit",
    "q",
    "ls",
    "curl",
    "shell",
    "context",
    "load",
    "context on",
    "context off",
    "context reset",
)


class CompletionKind(Enum):
    START = auto()
    META_COMMAND = auto()
    PATH = auto()
    REQ = auto()
    RESP = auto()
    REQPATH = auto()


@dataclass(frozen=True)
class CompletionContext:
    kind: CompletionKind
    partial: str
    completed_query: str
    method_filter: str | None = None


class RestXCompleter(Completer):
    """Custom completer that adapts to the current DSL parse state."""

    def __init__(self, spec: ParsedSpec, context: QueryContext) -> None:
        self.spec = spec
        self.context = context

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        del complete_event
        completion_context = detect_completion_context(document.text_before_cursor)
        endpoints = self._filtered_endpoints(completion_context.completed_query)

        if completion_context.method_filter:
            endpoints = [
                endpoint
                for endpoint in endpoints
                if endpoint.method == completion_context.method_filter
            ]

        candidates = self._candidates(completion_context, endpoints)
        partial = completion_context.partial
        current_term = split_query_terms(document.text_before_cursor)[1]
        replace_len = len(_active_fragment(current_term))

        for candidate in sorted(candidates, key=str.lower):
            if partial and not _candidate_matches_partial(candidate, partial):
                continue
            yield Completion(
                candidate,
                start_position=-replace_len,
            )

    def _filtered_endpoints(self, completed_query: str) -> list[Endpoint]:
        query = self._effective_query(completed_query)
        if not query.strip():
            return list(self.spec.endpoints)

        try:
            expr = parse_query(query)
        except QueryParseError:
            method = extract_method_filter(completed_query)
            if method:
                return [
                    endpoint
                    for endpoint in self.spec.endpoints
                    if endpoint.method == method
                ]
            return list(self.spec.endpoints)

        return filter_endpoints(expr, self.spec.endpoints, self.spec)

    def _effective_query(self, completed_query: str) -> str:
        if self.context.enabled and self.context.filter_text:
            if completed_query.strip():
                return f"{self.context.filter_text} AND {completed_query}"
            return self.context.filter_text
        return completed_query

    def _candidates(
        self,
        completion_context: CompletionContext,
        endpoints: list[Endpoint],
    ) -> set[str]:
        kind = completion_context.kind
        partial = completion_context.partial

        if kind == CompletionKind.START:
            values = set(HTTP_METHOD_CHOICES)
            values.update(FIELD_PREFIXES)
            values.update(_dot_command_candidates(partial))
            values.update(extract_open_parens_prefixes(completion_context.completed_query))
            return {
                value for value in values if _candidate_matches_partial(value, partial)
            }

        if kind == CompletionKind.META_COMMAND:
            return _dot_command_candidates(partial)

        if kind == CompletionKind.PATH:
            paths = {endpoint.path for endpoint in endpoints}
            return {path for path in paths if _matches_partial(path, partial)}

        if kind == CompletionKind.REQ:
            names = _input_parameter_names(endpoints)
            return {name for name in names if _matches_partial(name, partial)}

        if kind == CompletionKind.RESP:
            names = _response_field_names(endpoints, self.spec)
            return {name for name in names if _matches_partial(name, partial)}

        if kind == CompletionKind.REQPATH:
            names = _path_parameter_names(endpoints)
            return {name for name in names if _matches_partial(name, partial)}

        return set()


def detect_completion_context(text_before_cursor: str) -> CompletionContext:
    """Inspect text before the cursor and determine completion context."""
    if text_before_cursor.startswith("!"):
        return CompletionContext(
            kind=CompletionKind.META_COMMAND,
            partial=text_before_cursor[1:],
            completed_query="",
        )

    dot_context = _detect_dot_command_context(text_before_cursor)
    if dot_context is not None:
        return dot_context

    completed_query, current_term = split_query_terms(text_before_cursor)
    method_filter = extract_method_filter(completed_query)
    active_term = _active_fragment(current_term)

    if not active_term:
        if method_filter and _ends_after_method(completed_query):
            return CompletionContext(
                kind=CompletionKind.PATH,
                partial="",
                completed_query=completed_query,
                method_filter=method_filter,
            )
        return CompletionContext(
            kind=CompletionKind.START,
            partial="",
            completed_query=completed_query,
            method_filter=method_filter,
        )

    active_term = active_term.lstrip("(")

    if active_term.startswith("/"):
        return CompletionContext(
            kind=CompletionKind.PATH,
            partial=active_term,
            completed_query=completed_query,
            method_filter=method_filter,
        )

    prefix_match = re.match(r"^(req:|resp:|reqpath:)(.*)$", active_term, re.IGNORECASE)
    if prefix_match:
        prefix = prefix_match.group(1).lower()
        partial = prefix_match.group(2)
        kind = {
            "req:": CompletionKind.REQ,
            "resp:": CompletionKind.RESP,
            "reqpath:": CompletionKind.REQPATH,
        }[prefix]
        return CompletionContext(
            kind=kind,
            partial=partial,
            completed_query=completed_query,
            method_filter=method_filter,
        )

    upper = active_term.upper()
    if upper in HTTP_METHODS or any(
        method.startswith(upper) for method in HTTP_METHOD_CHOICES if upper
    ):
        return CompletionContext(
            kind=CompletionKind.START,
            partial=active_term,
            completed_query=completed_query,
            method_filter=method_filter,
        )

    for prefix in FIELD_PREFIXES:
        if prefix.startswith(active_term.lower()) and active_term:
            return CompletionContext(
                kind=CompletionKind.START,
                partial=active_term,
                completed_query=completed_query,
                method_filter=method_filter,
            )

    for command in REPL_COMMANDS:
        if _dot_command_matches_partial(command, active_term):
            return CompletionContext(
                kind=CompletionKind.START,
                partial=active_term,
                completed_query=completed_query,
                method_filter=method_filter,
            )

    return CompletionContext(
        kind=CompletionKind.START,
        partial=active_term,
        completed_query=completed_query,
        method_filter=method_filter,
    )


def _looks_like_meta_command_typing(body: str) -> bool:
    """Return True when input after ``.`` could still be a meta-command."""
    text = body.lower().strip()
    if not text:
        return True
    if text.startswith("context"):
        return True
    for command in _META_COMMAND_TYPING_HINTS:
        if command.startswith(text) or text.startswith(command.split()[0]):
            return True
    return False


def _detect_dot_command_context(text_before_cursor: str) -> CompletionContext | None:
    """Return completion context when the user is typing a . meta-command."""
    if not text_before_cursor.startswith("."):
        return None

    if text_before_cursor.startswith(".!"):
        return CompletionContext(
            kind=CompletionKind.META_COMMAND,
            partial=text_before_cursor[2:],
            completed_query="",
        )

    body = text_before_cursor[1:]
    if not _looks_like_meta_command_typing(body):
        return None

    return CompletionContext(
        kind=CompletionKind.META_COMMAND,
        partial=body,
        completed_query="",
    )


def _dot_command_body(command: str) -> str:
    """Return the command text after the leading dot."""
    if command.startswith(".!"):
        return "!"
    return command[1:]


def _dot_command_matches_partial(command: str, partial: str) -> bool:
    """Match dot commands by full text or by the body after ``.``."""
    if not partial:
        return True
    partial_lower = partial.lower()
    command_lower = command.lower()
    if command_lower.startswith(partial_lower):
        return True
    body = _dot_command_body(command).lower()
    return body.startswith(partial_lower.lstrip("."))


def _dot_command_candidates(partial: str) -> set[str]:
    """Return dot-prefixed meta-command completion candidates."""
    return {
        command
        for command in REPL_COMMANDS
        if _dot_command_matches_partial(command, partial)
    }


def _candidate_matches_partial(candidate: str, partial: str) -> bool:
    """Return True when a completion candidate matches the typed partial."""
    if not partial:
        return True
    if candidate.startswith("."):
        return _dot_command_matches_partial(candidate, partial)
    return candidate.lower().startswith(partial.lower())


def _active_fragment(term: str) -> str:
    """Return the fragment currently being edited within a term."""
    fragment = term.strip()
    for operator in ("&&", "||"):
        if operator in fragment:
            fragment = fragment.rsplit(operator, 1)[-1].strip()
    return fragment


def split_query_terms(text: str) -> tuple[str, str]:
    """Split input into completed query text and the current partial term."""
    if not text:
        return "", ""

    if text[-1].isspace() or text.endswith("&&") or text.endswith("||"):
        return text.rstrip(), ""

    depth = 0
    term_start = 0
    index = 0
    length = len(text)

    while index < length:
        if text.startswith("&&", index) or text.startswith("||", index):
            if depth == 0:
                term_start = index + 2
            index += 2
            continue

        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        elif char.isspace() and depth == 0:
            term_start = index + 1
        index += 1

    return text[:term_start].rstrip(), text[term_start:]


def extract_method_filter(completed_query: str) -> str | None:
    """Extract an HTTP method constraint from the completed portion of a query."""
    if not completed_query.strip():
        return None

    try:
        expr = parse_query(completed_query)
    except QueryParseError:
        return _bare_method_from_text(completed_query)

    return _method_from_expr(expr) or _bare_method_from_text(completed_query)


def _bare_method_from_text(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None

    first = stripped.split()[0]
    upper = first.upper()
    if upper in HTTP_METHODS:
        return upper
    return None


def _method_from_expr(expr) -> str | None:
    from restx.core.dsl_parser import AndExpr, Condition, FieldKind, MatchMode, OrExpr

    if isinstance(expr, Condition):
        if expr.field == FieldKind.METHOD and expr.mode == MatchMode.EXACT:
            return expr.value.upper()
        return None

    if isinstance(expr, AndExpr):
        for term in expr.terms:
            method = _method_from_expr(term)
            if method:
                return method
        return None

    if isinstance(expr, OrExpr):
        methods = {_method_from_expr(term) for term in expr.terms}
        methods.discard(None)
        if len(methods) == 1:
            return methods.pop()
        return None

    return None


def _ends_after_method(completed_query: str) -> bool:
    stripped = completed_query.rstrip()
    if not stripped:
        return False
    last = stripped.split()[-1].upper()
    return last in HTTP_METHODS


def extract_open_parens_prefixes(completed_query: str) -> set[str]:
    """Inside an open parenthesis group, offer start-of-line completions."""
    depth = 0
    for char in completed_query:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
    if depth > 0:
        return set(HTTP_METHOD_CHOICES) | set(FIELD_PREFIXES)
    return set()


def _matches_partial(value: str, partial: str) -> bool:
    if not partial:
        return True
    return value.lower().startswith(partial.lower())


def _input_parameter_names(endpoints: Iterable[Endpoint]) -> set[str]:
    names: set[str] = set()
    for endpoint in endpoints:
        for parameter in endpoint.parameters:
            if parameter.location != "path":
                names.add(parameter.name)
    return names


def _path_parameter_names(endpoints: Iterable[Endpoint]) -> set[str]:
    names: set[str] = set()
    for endpoint in endpoints:
        for parameter in endpoint.parameters:
            if parameter.location == "path":
                names.add(parameter.name)
    return names


def _response_field_names(endpoints: Iterable[Endpoint], spec: ParsedSpec) -> set[str]:
    from restx.core.matcher import _response_field_names as endpoint_resp_fields

    names: set[str] = set()
    for endpoint in endpoints:
        names.update(endpoint_resp_fields(endpoint, spec))
    return names
