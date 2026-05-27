"""Unit tests for contextual query mode."""

import os
import unittest

from restx.core import QueryContext, execute_query, load_spec_from_file
from restx.core.context import MatchResultState, and_expressions, parse_combined_query
from restx.core.dsl_parser import AndExpr, parse_query

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_dsl_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
    return load_spec_from_file(path)


class TestQueryContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_prompt_default_mode(self):
        context = QueryContext()
        self.assertEqual(context.prompt_suffix(), "restx> ")

    def test_prompt_context_mode_without_filter(self):
        context = QueryContext(enabled=True)
        self.assertEqual(context.prompt_suffix(), "restx (context)> ")

    def test_prompt_context_mode_with_filter(self):
        context = QueryContext(enabled=True, filter_parts=["GET /users*"])
        self.assertEqual(
            context.prompt_suffix(),
            "restx (context)> [GET /users*] ",
        )

    def test_context_accumulation_over_multiple_queries(self):
        context = QueryContext(enabled=True)
        first = context.execute("GET", self.spec)
        self.assertGreater(len(first), 0)
        self.assertEqual(context.filter_text, "GET")

        second = context.execute("/users*", self.spec)
        self.assertTrue(all(endpoint.path.startswith("/users") for endpoint in second))
        self.assertEqual(context.filter_text, "GET AND /users*")

    def test_reset_clears_filter_while_staying_enabled(self):
        context = QueryContext(enabled=True, filter_parts=["GET /users*"])
        context.reset()
        self.assertIsNone(context.filter_text)
        self.assertTrue(context.enabled)

    def test_toggle_off_on_preserves_filter(self):
        context = QueryContext(enabled=True, filter_parts=["GET /users*"])
        context.disable()
        self.assertFalse(context.enabled)
        context.enable()
        self.assertEqual(context.filter_text, "GET /users*")

    def test_mode_a_queries_are_independent(self):
        context = QueryContext(enabled=False, filter_parts=["GET /users*"])
        matches = execute_query("POST /users*", self.spec)
        self.assertEqual(context.filter_text, "GET /users*")
        paths = {endpoint.path for endpoint in matches}
        self.assertIn("/users", paths)
        self.assertIn("/users/bulk", paths)

    def test_status_lines_include_context_and_spec(self):
        context = QueryContext(enabled=True, filter_parts=["req:email"])
        lines = context.status_lines(self.spec)
        self.assertIn("Context mode: on", lines)
        self.assertIn("Context filter: req:email", lines)
        self.assertIn(f"Spec title: {self.spec.title}", lines)


class TestMatchResultState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_set_matches_clears_selection(self):
        state = MatchResultState()
        matches = execute_query("GET /users*", self.spec)
        state.set_matches(matches)
        state.select(1)
        self.assertEqual(state.selected_index, 0)

        state.set_matches(matches[:2])
        self.assertIsNone(state.selected_index)
        self.assertFalse(state.detail_open)
        self.assertEqual(state.match_count, 2)

    def test_select_and_reselect_without_new_query(self):
        state = MatchResultState()
        matches = execute_query("GET /users*", self.spec)
        state.set_matches(matches)

        first = state.select(1)
        second = state.select(2)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.path, matches[0].path)
        self.assertEqual(second.path, matches[1].path)
        self.assertEqual(state.match_count, len(matches))

    def test_close_detail_retains_match_list(self):
        state = MatchResultState()
        matches = execute_query("GET /users*", self.spec)
        state.set_matches(matches)
        state.select(2)
        state.close_detail()

        self.assertFalse(state.detail_open)
        self.assertIsNone(state.selected_index)
        self.assertEqual(state.match_count, len(matches))
        self.assertTrue(state.has_matches)


class TestContextExpressionHelpers(unittest.TestCase):
    def test_and_expressions_flattens_existing_and_nodes(self):
        left = parse_query("GET && /users*")
        right = parse_query("req:email")
        combined = and_expressions(left, right)
        self.assertIsInstance(combined, AndExpr)
        self.assertEqual(len(combined.terms), 3)

    def test_parse_combined_query_without_context(self):
        expr = parse_combined_query(None, "GET")
        self.assertEqual(expr, parse_query("GET"))

    def test_parse_combined_query_with_context(self):
        expr = parse_combined_query("GET", "/users*")
        self.assertIsInstance(expr, AndExpr)


if __name__ == "__main__":
    unittest.main()
