"""Unit tests for endpoint drill-down tree view content."""

import os
import unittest

from restx.cli.tree_view import (
    build_endpoint_tree,
    build_inline_endpoint_details,
    collect_visible_display_labels,
    collect_visible_labels,
    find_schema_node_by_label,
    format_detail_node_label,
    is_escape_key,
    parse_selection,
    selection_error,
    toggle_detail_node,
    truncate_description,
    truncate_endpoint_summary,
    tree_line_prefix_len,
)
from restx.core import load_spec_from_file

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_dsl_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
    return load_spec_from_file(path)


def _load_petstore_user_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_petstore_user.json")
    return load_spec_from_file(path)


def _load_curl_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_curl.json")
    return load_spec_from_file(path)


class TestTreeViewContent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()
        cls.endpoint = next(
            endpoint
            for endpoint in cls.spec.endpoints
            if endpoint.method == "GET" and endpoint.path == "/users/{id}"
        )

    def test_tree_includes_parameters_and_responses(self):
        tree = build_endpoint_tree(self.endpoint, self.spec)
        labels = collect_visible_labels(tree)

        self.assertIn("Parameters", labels)
        self.assertTrue(any("id (path, required, integer)" in label for label in labels))
        self.assertTrue(any("email (query, optional, string)" in label for label in labels))
        self.assertIn("Response 200", labels)
        self.assertIn("Schema: User object, 7 fields", labels)
        self.assertFalse(any("role (string" in label for label in labels))
        self.assertNotIn("curl command:", labels)
        self.assertFalse(any("curl -X GET" in label for label in labels))

    def test_response_schema_collapsed_by_default(self):
        details = build_inline_endpoint_details(self.endpoint, self.spec)
        display_labels = collect_visible_display_labels(details)
        joined = "\n".join(display_labels)
        self.assertIn("▶ Schema: User object, 7 fields", joined)
        self.assertNotIn("id (integer", joined)

    def test_tree_excludes_curl_command(self):
        tree = build_endpoint_tree(self.endpoint, self.spec)
        labels = collect_visible_labels(tree)
        self.assertFalse(any("Accept: application/json" in label for label in labels))

    def test_parse_selection_commands(self):
        self.assertEqual(parse_selection("2"), 2)
        self.assertEqual(parse_selection("select 3"), 3)
        self.assertIsNone(parse_selection("GET /users*"))

    def test_selection_error_messages(self):
        self.assertIn("Run a query first", selection_error(1, 0))
        self.assertIn("between 1 and 3", selection_error(5, 3))

    def test_truncate_description_appends_ellipsis(self):
        text = "Creates list of users with given input array in the system"
        truncated = truncate_description(text, 30)
        self.assertLessEqual(len(truncated), 30)
        self.assertTrue(truncated.endswith("..."))
        self.assertTrue(truncated.startswith("Creates list"))

    def test_truncate_description_leaves_short_text_unchanged(self):
        text = "Create user"
        self.assertEqual(truncate_description(text, 40), text)

    def test_truncate_endpoint_summary_respects_line_budget(self):
        summary = "Creates list of users with given input array in the system"
        truncated = truncate_endpoint_summary(
            "POST",
            "/user/createWithList",
            summary,
            max_line_width=40,
            tree_prefix_len=tree_line_prefix_len(2),
        )
        self.assertLessEqual(
            len(f"POST /user/createWithList — {truncated}"),
            40 - tree_line_prefix_len(2),
        )
        self.assertTrue(truncated.endswith("..."))

    def test_escape_key_helper(self):
        self.assertTrue(is_escape_key("\x1b"))
        self.assertFalse(is_escape_key("\x1b[A"))


class TestSchemaDrillDown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_petstore_user_fixture()
        cls.post_user = next(
            endpoint
            for endpoint in cls.spec.endpoints
            if endpoint.method == "POST" and endpoint.path == "/user"
        )

    def test_body_schema_collapsed_with_indicator(self):
        details = build_inline_endpoint_details(self.post_user, self.spec)
        body_node = find_schema_node_by_label(details, "body (User object, 8 fields)")
        self.assertIsNotNone(body_node)
        assert body_node is not None
        self.assertFalse(body_node.expanded)
        self.assertEqual(
            format_detail_node_label(body_node),
            "▶ body (User object, 8 fields)",
        )

    def test_toggle_expansion_reveals_immediate_children(self):
        details = build_inline_endpoint_details(self.post_user, self.spec)
        body_node = find_schema_node_by_label(details, "body (User object, 8 fields)")
        assert body_node is not None

        toggle_detail_node(body_node)
        self.assertTrue(body_node.expanded)
        self.assertEqual(
            format_detail_node_label(body_node),
            "▼ body (User object, 8 fields)",
        )

        labels = collect_visible_labels(details)
        self.assertIn("id (integer, int64, optional)", labels)
        self.assertIn("username (string, required)", labels)
        self.assertIn("userStatus (integer, int32, optional)", labels)

    def test_toggle_collapses_schema_node(self):
        details = build_inline_endpoint_details(self.post_user, self.spec)
        body_node = find_schema_node_by_label(details, "body (User object, 8 fields)")
        assert body_node is not None

        toggle_detail_node(body_node)
        toggle_detail_node(body_node)

        self.assertFalse(body_node.expanded)
        labels = collect_visible_labels(details)
        self.assertNotIn("username (string, required)", labels)

    def test_expansion_state_persists_during_session(self):
        details = build_inline_endpoint_details(self.post_user, self.spec)
        body_node = find_schema_node_by_label(details, "body (User object, 8 fields)")
        assert body_node is not None

        toggle_detail_node(body_node)
        first_pass = collect_visible_display_labels(details)

        toggle_detail_node(body_node)
        second_pass = collect_visible_display_labels(details)

        toggle_detail_node(body_node)
        third_pass = collect_visible_display_labels(details)

        self.assertIn("▼ body (User object, 8 fields)", first_pass)
        self.assertIn("▶ body (User object, 8 fields)", second_pass)
        self.assertIn("▼ body (User object, 8 fields)", third_pass)
        self.assertEqual(first_pass, third_pass)


class TestNestedSchemaDrillDown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_curl_fixture()
        cls.post_items = next(
            endpoint
            for endpoint in cls.spec.endpoints
            if endpoint.method == "POST" and endpoint.path == "/items"
        )

    def test_nested_object_child_starts_collapsed_when_parent_expanded(self):
        details = build_inline_endpoint_details(self.post_items, self.spec)
        body_node = find_schema_node_by_label(details, "body (Item object, 2 fields)")
        assert body_node is not None
        toggle_detail_node(body_node)

        address_node = find_schema_node_by_label(
            details,
            "address (Address object, 3 fields)",
        )
        self.assertIsNotNone(address_node)
        assert address_node is not None
        self.assertFalse(address_node.expanded)
        self.assertEqual(
            format_detail_node_label(address_node),
            "▶ address (Address object, 3 fields)",
        )

        display_labels = collect_visible_display_labels(details)
        self.assertIn("name (string, optional)", display_labels)
        self.assertNotIn("street (string, optional)", display_labels)

    def test_nested_object_expands_one_level_at_a_time(self):
        details = build_inline_endpoint_details(self.post_items, self.spec)
        body_node = find_schema_node_by_label(details, "body (Item object, 2 fields)")
        assert body_node is not None
        toggle_detail_node(body_node)

        address_node = find_schema_node_by_label(
            details,
            "address (Address object, 3 fields)",
        )
        assert address_node is not None
        toggle_detail_node(address_node)

        labels = collect_visible_labels(details)
        self.assertIn("street (string, optional)", labels)
        self.assertIn("city (string, optional)", labels)
        self.assertIn("zipCode (integer, optional)", labels)


if __name__ == "__main__":
    unittest.main()
