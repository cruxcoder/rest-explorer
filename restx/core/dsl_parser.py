"""Query DSL tokenizer and parser."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Sequence

from .errors import QueryParseError

HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE"}
)


class FieldKind(Enum):
    METHOD = auto()
    PATH = auto()
    TEXT = auto()
    REQ = auto()
    REQPATH = auto()
    RESP = auto()


class MatchMode(Enum):
    GLOB = auto()
    EXACT = auto()
    REGEX = auto()
    NOT_EQUAL = auto()


@dataclass(frozen=True)
class Condition:
    field: FieldKind
    mode: MatchMode
    value: str


@dataclass(frozen=True)
class AndExpr:
    terms: tuple[Expr, ...]


@dataclass(frozen=True)
class OrExpr:
    terms: tuple[Expr, ...]


Expr = Condition | AndExpr | OrExpr


class _TokenKind(Enum):
    METHOD = auto()
    PATH = auto()
    PREFIX = auto()
    IDENT = auto()
    OP_EQ = auto()
    OP_NE = auto()
    OP_REGEX = auto()
    AND = auto()
    OR = auto()
    LPAREN = auto()
    RPAREN = auto()
    EOF = auto()


@dataclass(frozen=True)
class _Token:
    kind: _TokenKind
    value: str
    start: int
    end: int


_PREFIX_FIELDS = {
    "req": FieldKind.REQ,
    "resp": FieldKind.RESP,
    "reqpath": FieldKind.REQPATH,
}


def parse_query(query: str) -> Expr:
    """Parse a query string into an expression tree."""
    text = query.strip()
    if not text:
        raise QueryParseError("Parse error: empty query.")

    tokens = _tokenize(text)
    parser = _Parser(tokens, text)
    return parser.parse()


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    length = len(text)

    while index < length:
        if text[index].isspace():
            index += 1
            continue

        start = index

        if text.startswith("==", index):
            tokens.append(_Token(_TokenKind.OP_EQ, "==", start, start + 2))
            index += 2
            continue
        if text.startswith("!=", index):
            tokens.append(_Token(_TokenKind.OP_NE, "!=", start, start + 2))
            index += 2
            continue
        if text.startswith("&&", index):
            tokens.append(_Token(_TokenKind.AND, "&&", start, start + 2))
            index += 2
            continue
        if text.startswith("||", index):
            tokens.append(_Token(_TokenKind.OR, "||", start, start + 2))
            index += 2
            continue

        char = text[index]
        if char == "~":
            tokens.append(_Token(_TokenKind.OP_REGEX, "~", start, start + 1))
            index += 1
            continue
        if char == "(":
            tokens.append(_Token(_TokenKind.LPAREN, "(", start, start + 1))
            index += 1
            continue
        if char == ")":
            tokens.append(_Token(_TokenKind.RPAREN, ")", start, start + 1))
            index += 1
            continue

        if char == "/":
            index = _read_path(text, start)
            tokens.append(_Token(_TokenKind.PATH, text[start:index], start, index))
            continue

        if char.isalpha() or char == "_":
            index = _read_word(text, start)
            word = text[start:index]

            if word == "method":
                tokens.append(_Token(_TokenKind.IDENT, word, start, index))
                continue
            if word == "path":
                tokens.append(_Token(_TokenKind.IDENT, word, start, index))
                continue

            if word in _PREFIX_FIELDS:
                if index < length and text[index] == ":":
                    if index + 1 < length and text[index + 1] == ":":
                        hint = _peek_pattern_hint(text, index + 2)
                        raise QueryParseError(
                            f"Parse error near '{word}::' — unexpected ':'. "
                            f"Did you mean '{word}:{hint}'?",
                            suggestion=f"{word}:{hint}",
                        )
                    prefix_end = index + 1
                    pattern_start = prefix_end
                    pattern_end = _read_field_pattern(text, pattern_start)
                    tokens.append(
                        _Token(_TokenKind.PREFIX, word, start, prefix_end)
                    )
                    if pattern_end == pattern_start:
                        raise QueryParseError(
                            f"Parse error near '{word}:' — expected a pattern after the prefix.",
                        )
                    tokens.append(
                        _Token(
                            _TokenKind.IDENT,
                            text[pattern_start:pattern_end],
                            pattern_start,
                            pattern_end,
                        )
                    )
                    index = pattern_end
                    continue

            if word.upper() in HTTP_METHODS:
                tokens.append(_Token(_TokenKind.METHOD, word.upper(), start, index))
                continue

            tokens.append(_Token(_TokenKind.IDENT, word, start, index))
            continue

        if char in ".^":
            index = _read_field_pattern(text, start)
            tokens.append(_Token(_TokenKind.IDENT, text[start:index], start, index))
            continue

        raise QueryParseError(
            f"Parse error near '{text[start:start + 8]}' — unexpected character '{char}'.",
        )

    tokens.append(_Token(_TokenKind.EOF, "", length, length))
    return tokens


def _read_word(text: str, start: int) -> int:
    index = start
    while index < len(text) and (text[index].isalnum() or text[index] == "_"):
        index += 1
    return index


def _read_field_pattern(text: str, start: int) -> int:
    index = start
    while index < len(text):
        if text[index].isspace():
            break
        if text.startswith("&&", index) or text.startswith("||", index):
            break
        if text[index] in "()":
            break
        index += 1
    return index


def _read_path(text: str, start: int) -> int:
    index = start + 1
    while index < len(text):
        char = text[index]
        if char.isspace():
            break
        if text.startswith("&&", index) or text.startswith("||", index):
            break
        if char in "()":
            break
        if char == "~" and index > start:
            break
        index += 1
    return index


def _peek_pattern_hint(text: str, index: int) -> str:
    while index < len(text) and text[index].isspace():
        index += 1
    end = index
    while end < len(text) and not text[end].isspace() and text[end] not in "&|()":
        end += 1
    return text[index:end] or "value"


class _Parser:
    def __init__(self, tokens: Sequence[_Token], source: str) -> None:
        self._tokens = list(tokens)
        self._source = source
        self._index = 0

    def parse(self) -> Expr:
        expr = self._parse_and_expr(allow_or=False)
        self._expect(_TokenKind.EOF)
        return expr

    def _current(self) -> _Token:
        return self._tokens[self._index]

    def _advance(self) -> _Token:
        token = self._current()
        if token.kind != _TokenKind.EOF:
            self._index += 1
        return token

    def _expect(self, kind: _TokenKind) -> _Token:
        token = self._current()
        if token.kind != kind:
            self._unexpected(token)
        return self._advance()

    def _unexpected(self, token: _Token) -> None:
        snippet = self._source[token.start : token.end] or self._source[token.start : token.start + 8]
        raise QueryParseError(
            f"Parse error near '{snippet}' — unexpected token.",
        )

    def _parse_and_expr(self, *, allow_or: bool) -> Expr:
        terms: list[Expr] = [self._parse_or_group(allow_or=allow_or)]

        while True:
            token = self._current()
            if token.kind == _TokenKind.AND:
                self._advance()
                terms.append(self._parse_or_group(allow_or=allow_or))
                continue
            if token.kind == _TokenKind.OR:
                if not allow_or:
                    raise QueryParseError(
                        "Parse error near '||' — OR expressions must be wrapped in parentheses.",
                    )
                break
            if token.kind in (_TokenKind.EOF, _TokenKind.RPAREN):
                break
            if token.kind in (
                _TokenKind.METHOD,
                _TokenKind.PATH,
                _TokenKind.PREFIX,
                _TokenKind.IDENT,
                _TokenKind.LPAREN,
            ):
                terms.append(self._parse_or_group(allow_or=allow_or))
                continue
            break

        if len(terms) == 1:
            return terms[0]
        return AndExpr(tuple(terms))

    def _parse_or_group(self, *, allow_or: bool) -> Expr:
        token = self._current()
        if token.kind == _TokenKind.LPAREN:
            self._advance()
            inner = self._parse_or_expr()
            self._expect(_TokenKind.RPAREN)
            return inner

        if token.kind == _TokenKind.OR:
            raise QueryParseError(
                "Parse error near '||' — OR expressions must be wrapped in parentheses.",
            )

        return self._parse_primary()

    def _parse_or_expr(self) -> Expr:
        terms: list[Expr] = [self._parse_and_expr(allow_or=True)]

        while self._current().kind == _TokenKind.OR:
            self._advance()
            terms.append(self._parse_and_expr(allow_or=True))

        if len(terms) == 1:
            return terms[0]
        return OrExpr(tuple(terms))

    def _parse_primary(self) -> Expr:
        token = self._current()

        if token.kind == _TokenKind.METHOD:
            self._advance()
            return Condition(FieldKind.METHOD, MatchMode.EXACT, token.value)

        if token.kind == _TokenKind.PATH:
            self._advance()
            return Condition(
                FieldKind.PATH,
                detect_match_mode(token.value),
                token.value,
            )

        if token.kind == _TokenKind.PREFIX:
            return self._parse_prefixed(token)

        if token.kind == _TokenKind.IDENT:
            if token.value == "method":
                return self._parse_field_condition(FieldKind.METHOD)
            if token.value == "path":
                return self._parse_field_condition(FieldKind.PATH)
            self._advance()
            return Condition(
                FieldKind.TEXT,
                detect_match_mode(token.value),
                token.value,
            )

        self._unexpected(token)

    def _parse_prefixed(self, prefix_token: _Token) -> Condition:
        field = _PREFIX_FIELDS[prefix_token.value]
        self._advance()
        pattern = self._expect(_TokenKind.IDENT).value
        return Condition(field, detect_match_mode(pattern), pattern)

    def _parse_field_condition(self, field: FieldKind) -> Condition:
        self._advance()
        token = self._current()

        if token.kind == _TokenKind.OP_EQ:
            self._advance()
            value = self._read_pattern_value()
            return Condition(field, MatchMode.EXACT, value)

        if token.kind == _TokenKind.OP_NE:
            self._advance()
            value = self._read_pattern_value()
            return Condition(field, MatchMode.NOT_EQUAL, value)

        if token.kind == _TokenKind.OP_REGEX:
            self._advance()
            value = self._read_pattern_value()
            return Condition(field, MatchMode.REGEX, value)

        if token.kind in (_TokenKind.METHOD, _TokenKind.PATH, _TokenKind.IDENT):
            value = self._read_pattern_value()
            return Condition(field, detect_match_mode(value), value)

        self._unexpected(token)

    def _read_pattern_value(self) -> str:
        token = self._current()
        if token.kind == _TokenKind.METHOD:
            self._advance()
            return token.value
        if token.kind == _TokenKind.PATH:
            self._advance()
            return token.value
        if token.kind == _TokenKind.IDENT:
            self._advance()
            return token.value
        self._unexpected(token)


def detect_match_mode(pattern: str) -> MatchMode:
    """Choose glob vs regex from pattern content per PRD §5.3."""
    index = 0
    length = len(pattern)

    while index < length:
        char = pattern[index]
        if char == "\\" and index + 1 < length:
            index += 2
            continue

        if char in "*?[":
            index += 1
            continue

        if char in ".+^$()|{}":
            return MatchMode.REGEX

        index += 1

    return MatchMode.GLOB
