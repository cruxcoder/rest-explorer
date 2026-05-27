"""Unit tests for the Query DSL parser."""

import unittest

from restx.core.dsl_parser import (
    AndExpr,
    Condition,
    FieldKind,
    MatchMode,
    OrExpr,
    detect_match_mode,
    parse_query,
)
from restx.core.errors import QueryParseError


class TestDetectMatchMode(unittest.TestCase):
    def test_glob_only(self):
        self.assertEqual(detect_match_mode("/users*"), MatchMode.GLOB)
        self.assertEqual(detect_match_mode("owner*"), MatchMode.GLOB)

    def test_regex_metacharacters(self):
        self.assertEqual(detect_match_mode("dogna.*"), MatchMode.REGEX)
        self.assertEqual(detect_match_mode("/users/woof.*"), MatchMode.REGEX)

    def test_escaped_dot_in_glob(self):
        self.assertEqual(detect_match_mode(r"v1\.0*"), MatchMode.GLOB)


class TestParseQuery(unittest.TestCase):
    def test_bare_method(self):
        expr = parse_query("GET")
        self.assertIsInstance(expr, Condition)
        self.assertEqual(expr.field, FieldKind.METHOD)
        self.assertEqual(expr.mode, MatchMode.EXACT)
        self.assertEqual(expr.value, "GET")

    def test_bare_path_glob(self):
        expr = parse_query("/users*")
        self.assertIsInstance(expr, Condition)
        self.assertEqual(expr.field, FieldKind.PATH)
        self.assertEqual(expr.mode, MatchMode.GLOB)
        self.assertEqual(expr.value, "/users*")

    def test_implicit_and(self):
        expr = parse_query("GET /users*")
        self.assertIsInstance(expr, AndExpr)
        self.assertEqual(len(expr.terms), 2)
        self.assertEqual(expr.terms[0].value, "GET")
        self.assertEqual(expr.terms[1].value, "/users*")

    def test_explicit_and(self):
        expr = parse_query("GET && /users*")
        self.assertIsInstance(expr, AndExpr)
        self.assertEqual(len(expr.terms), 2)

    def test_method_exact_operator(self):
        expr = parse_query("method == GET")
        self.assertIsInstance(expr, Condition)
        self.assertEqual(expr.field, FieldKind.METHOD)
        self.assertEqual(expr.mode, MatchMode.EXACT)

    def test_method_not_equal(self):
        expr = parse_query("method != DELETE")
        self.assertEqual(expr.mode, MatchMode.NOT_EQUAL)
        self.assertEqual(expr.value, "DELETE")

    def test_path_regex_operator(self):
        expr = parse_query("path ~ /users/woof.*")
        self.assertEqual(expr.field, FieldKind.PATH)
        self.assertEqual(expr.mode, MatchMode.REGEX)
        self.assertEqual(expr.value, "/users/woof.*")

    def test_req_prefix(self):
        expr = parse_query("req:email")
        self.assertEqual(expr.field, FieldKind.REQ)
        self.assertEqual(expr.value, "email")

    def test_resp_prefix_glob(self):
        expr = parse_query("resp:owner*")
        self.assertEqual(expr.field, FieldKind.RESP)
        self.assertEqual(expr.mode, MatchMode.GLOB)

    def test_reqpath_prefix(self):
        expr = parse_query("reqpath:id")
        self.assertEqual(expr.field, FieldKind.REQPATH)
        self.assertEqual(expr.value, "id")

    def test_parenthesized_or(self):
        expr = parse_query("(resp:city* || resp:state)")
        self.assertIsInstance(expr, OrExpr)
        self.assertEqual(len(expr.terms), 2)

    def test_grouped_precedence(self):
        expr = parse_query("(GET || POST) && /users*")
        self.assertIsInstance(expr, AndExpr)
        self.assertIsInstance(expr.terms[0], OrExpr)
        self.assertEqual(expr.terms[1].value, "/users*")

    def test_nested_parens(self):
        expr = parse_query("((GET))")
        self.assertIsInstance(expr, Condition)
        self.assertEqual(expr.value, "GET")

    def test_invalid_double_colon(self):
        with self.assertRaises(QueryParseError) as ctx:
            parse_query("req::email")

        message = str(ctx.exception)
        self.assertIn("req::", message)
        self.assertIn("Did you mean 'req:email'?", message)

    def test_or_without_parens_is_error(self):
        with self.assertRaises(QueryParseError) as ctx:
            parse_query("resp:city* || resp:state")

        self.assertIn("parentheses", str(ctx.exception).lower())

    def test_req_regex_pattern(self):
        expr = parse_query("req:dogna.*")
        self.assertEqual(expr.field, FieldKind.REQ)
        self.assertEqual(expr.mode, MatchMode.REGEX)
        self.assertEqual(expr.value, "dogna.*")

    def test_bare_free_text(self):
        expr = parse_query("logout")
        self.assertIsInstance(expr, Condition)
        self.assertEqual(expr.field, FieldKind.TEXT)
        self.assertEqual(expr.mode, MatchMode.GLOB)
        self.assertEqual(expr.value, "logout")

    def test_method_and_free_text(self):
        expr = parse_query("GET logout")
        self.assertIsInstance(expr, AndExpr)
        self.assertEqual(len(expr.terms), 2)
        self.assertEqual(expr.terms[0].field, FieldKind.METHOD)
        self.assertEqual(expr.terms[1].field, FieldKind.TEXT)

    def test_regex_starting_with_dot(self):
        expr = parse_query(".+")
        self.assertIsInstance(expr, Condition)
        self.assertEqual(expr.field, FieldKind.TEXT)
        self.assertEqual(expr.mode, MatchMode.REGEX)
        self.assertEqual(expr.value, ".+")

    def test_regex_starting_with_caret(self):
        expr = parse_query("^/users")
        self.assertIsInstance(expr, Condition)
        self.assertEqual(expr.field, FieldKind.TEXT)
        self.assertEqual(expr.mode, MatchMode.REGEX)
        self.assertEqual(expr.value, "^/users")

    def test_regex_dot_and_path_fragment(self):
        expr = parse_query(".+/order")
        self.assertIsInstance(expr, Condition)
        self.assertEqual(expr.field, FieldKind.TEXT)
        self.assertEqual(expr.mode, MatchMode.REGEX)
        self.assertEqual(expr.value, ".+/order")

    def test_caret_alone(self):
        expr = parse_query("^")
        self.assertIsInstance(expr, Condition)
        self.assertEqual(expr.field, FieldKind.TEXT)
        self.assertEqual(expr.mode, MatchMode.REGEX)
        self.assertEqual(expr.value, "^")


if __name__ == "__main__":
    unittest.main()
