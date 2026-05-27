"""Unit tests for the interactive API viewer skeleton."""

import os
import unittest

from restx.cli.colors import (
    METHOD_STYLES,
    PAGER_SELECTED_STYLE,
    SEARCH_HIGHLIGHT_STYLE,
    create_console,
    format_match_results_text,
)
from restx.cli.match_list import (
    format_match_list_lines,
    match_list_header_width,
    params_column_index,
)
from restx.cli.viewer import (
    build_endpoint_curl_command,
    collect_rendered_root_labels,
    curl_block_is_copy_pasteable,
    extract_curl_block_from_detail_output,
    format_endpoint_detail_output,
    render_api_tree,
    render_endpoint_detail_output,
    render_endpoint_markup,
    render_tree_to_text,
)
from restx.core import execute_query, load_spec_from_file
from restx.core.matcher import format_match_results, format_params
from restx.core.viewer import (
    SearchDirection,
    SearchState,
    apply_search_expansions,
    build_api_tree,
    clear_temporary_expansions,
    collect_root_display_labels,
    collect_tree_entries,
    commit_search,
    endpoint_label,
    exit_search,
    find_node_by_path,
    find_search_matches,
    flatten_visible_entries,
    goto_next_match,
    goto_previous_match,
    match_viewer_search,
    parent_path,
    start_search_input,
    toggle_search_case_sensitivity,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_dsl_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
    return load_spec_from_file(path)


class TestViewerTreeModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()
        cls.roots = build_api_tree(cls.spec)

    def test_parent_path(self):
        self.assertIsNone(parent_path("/users"))
        self.assertEqual(parent_path("/users/{id}"), "/users")
        self.assertEqual(parent_path("/users/bulk"), "/users")

    def test_build_api_tree_root_paths(self):
        root_paths = [node.path for node in self.roots]
        self.assertEqual(
            root_paths,
            ["/dogs", "/items", "/user/{name}", "/users"],
        )

    def test_users_node_has_collapsed_children(self):
        users_node = next(node for node in self.roots if node.path == "/users")
        child_paths = [child.path for child in users_node.children]
        self.assertEqual(child_paths, ["/users/bulk", "/users/{id}"])
        self.assertFalse(users_node.expanded)

    def test_root_display_labels_exclude_child_paths(self):
        labels = collect_root_display_labels(self.roots)
        joined = "\n".join(labels)
        self.assertIn("▶ /users", joined)
        self.assertIn("GET /users", joined)
        self.assertIn("POST /users", joined)
        self.assertNotIn("/users/{id}", joined)
        self.assertNotIn("/users/bulk", joined)

    def test_endpoint_label_includes_summary_when_present(self):
        endpoint = next(
            ep
            for ep in self.spec.endpoints
            if ep.method == "GET" and ep.path == "/users/{id}"
        )
        endpoint_with_summary = endpoint
        if endpoint.summary:
            self.assertIn("—", endpoint_label(endpoint_with_summary))


class TestViewerRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()
        cls.roots = build_api_tree(cls.spec)

    def test_render_api_tree_is_rich_tree(self):
        from rich.tree import Tree

        tree = render_api_tree(self.roots)
        self.assertIsInstance(tree, Tree)
        self.assertIn("API Endpoints", str(tree.label))

    def test_rendered_root_labels_include_direct_endpoints_only(self):
        labels = collect_rendered_root_labels(self.roots)
        self.assertIn("/users", labels)
        self.assertTrue(any(label.startswith("GET /users") for label in labels))
        self.assertTrue(any(label.startswith("POST /users") for label in labels))
        self.assertFalse(any("/users/{id}" in label for label in labels))

    def test_method_markup_uses_prds_color_styles(self):
        get_endpoint = next(
            ep for ep in self.spec.endpoints if ep.method == "GET" and ep.path == "/users"
        )
        post_endpoint = next(
            ep
            for ep in self.spec.endpoints
            if ep.method == "POST" and ep.path == "/users"
        )

        get_style = METHOD_STYLES["GET"]
        post_style = METHOD_STYLES["POST"]

        self.assertIn(f"[{get_style}]GET[/{get_style}]", render_endpoint_markup(get_endpoint))
        self.assertIn(
            f"[{post_style}]POST[/{post_style}]",
            render_endpoint_markup(post_endpoint),
        )

    def test_render_endpoint_highlight_and_active_markup(self):
        endpoint = next(
            ep for ep in self.spec.endpoints if ep.method == "GET" and ep.path == "/users"
        )
        highlighted = render_endpoint_markup(endpoint, highlighted=True)
        active = render_endpoint_markup(endpoint, active=True)
        self.assertIn(f"[{SEARCH_HIGHLIGHT_STYLE}]", highlighted)
        self.assertIn(PAGER_SELECTED_STYLE, active)

    def test_render_endpoint_markup_truncates_long_summary(self):
        from restx.core.models import Endpoint

        endpoint = Endpoint(
            method="POST",
            path="/user/createWithList",
            summary="Creates list of users with given input array in the system",
            parameters=[],
            responses=[],
        )
        markup = render_endpoint_markup(
            endpoint,
            max_line_width=40,
            tree_prefix_len=8,
        )
        plain = markup.replace("[/bright_yellow]", "").replace("[bright_yellow]", "")
        plain = plain.replace("[/bright_cyan]", "").replace("[bright_cyan]", "")
        self.assertIn("...", plain)
        self.assertLessEqual(len(plain.split(" — ", 1)[-1]), 40 - 8 - len("POST /user/createWithList — "))

    def test_rendered_tree_truncates_long_descriptions(self):
        from restx.core.models import Endpoint, ParsedSpec

        long_summary = (
            "Creates list of users with given input array in the system repository"
        )
        endpoint = Endpoint(
            method="POST",
            path="/user/createWithList",
            summary=long_summary,
            parameters=[],
            responses=[],
        )
        spec = ParsedSpec(
            title="Truncation Test",
            version="1.0",
            openapi_version="3.0.3",
            endpoints=[endpoint],
        )
        console = create_console(color_enabled=False)
        tree = render_api_tree(
            build_api_tree(spec),
            content_width=45,
        )
        rendered = render_tree_to_text(tree, console, 45)
        self.assertIn("...", rendered)
        for line in rendered.splitlines():
            if long_summary[:10] in line:
                self.assertLessEqual(len(line), 45)


class TestViewerSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()
        cls.roots = build_api_tree(cls.spec)
        cls.entries = collect_tree_entries(cls.roots)

    def setUp(self):
        def reset_expansion(nodes: list) -> None:
            for node in nodes:
                node.expanded = False
                reset_expansion(node.children)

        reset_expansion(self.roots)

    def test_start_search_input_sets_forward_and_backward_modes(self):
        forward = start_search_input(forward=True)
        backward = start_search_input(forward=False)

        self.assertTrue(forward.input_mode)
        self.assertTrue(forward.forward)
        self.assertTrue(backward.input_mode)
        self.assertFalse(backward.forward)

    def test_match_viewer_search_glob_pattern(self):
        self.assertTrue(match_viewer_search("/users/{id}", "users*"))
        self.assertTrue(match_viewer_search("GET /users", "GET /users*"))
        self.assertFalse(match_viewer_search("/dogs", "users*"))

    def test_match_viewer_search_regex_pattern(self):
        self.assertTrue(match_viewer_search("/users/{id}", r"/users/\{id\}"))
        self.assertTrue(match_viewer_search("DELETE /users/{id}", "DELETE.*id"))

    def test_match_viewer_search_plain_substring(self):
        self.assertTrue(match_viewer_search("GET /users", "users"))
        self.assertFalse(match_viewer_search("GET /dogs", "users"))

    def test_match_viewer_search_case_sensitivity(self):
        self.assertTrue(match_viewer_search("GET /users", "get", case_sensitive=False))
        self.assertFalse(match_viewer_search("GET /users", "get", case_sensitive=True))

    def test_find_search_matches_returns_entry_indices(self):
        matches = find_search_matches(self.entries, "/users*")
        matched_labels = [self.entries[index].label for index in matches]
        self.assertIn("/users", matched_labels)
        self.assertIn("GET /users", matched_labels)
        self.assertTrue(any("/users/{id}" in label for label in matched_labels))

    def test_commit_search_forward_starts_at_first_match(self):
        state = start_search_input(forward=True)
        state.input_buffer = "users*"
        state = commit_search(state, self.entries, self.roots)
        self.assertTrue(state.active)
        self.assertEqual(state.current_match, 0)
        self.assertGreater(len(state.match_indices), 1)

    def test_commit_search_backward_starts_at_last_match(self):
        state = start_search_input(forward=False)
        state.input_buffer = "users*"
        state = commit_search(state, self.entries, self.roots)
        self.assertEqual(state.current_match, len(state.match_indices) - 1)

    def test_goto_next_and_previous_forward_search(self):
        state = start_search_input(forward=True)
        state.input_buffer = "users*"
        state = commit_search(state, self.entries, self.roots)
        first_index = state.match_indices[state.current_match]

        state = goto_next_match(state, self.entries, self.roots)
        second_index = state.match_indices[state.current_match]
        self.assertNotEqual(first_index, second_index)

        state = goto_previous_match(state, self.entries, self.roots)
        self.assertEqual(state.match_indices[state.current_match], first_index)

    def test_goto_next_and_previous_backward_search(self):
        state = start_search_input(forward=False)
        state.input_buffer = "users*"
        state = commit_search(state, self.entries, self.roots)
        last_index = state.match_indices[state.current_match]

        state = goto_next_match(state, self.entries, self.roots)
        previous_index = state.match_indices[state.current_match]
        self.assertNotEqual(last_index, previous_index)

        state = goto_previous_match(state, self.entries, self.roots)
        self.assertEqual(state.match_indices[state.current_match], last_index)

    def test_search_expands_path_to_reveal_match(self):
        users_node = find_node_by_path(self.roots, "/users")
        self.assertFalse(users_node.expanded)

        state = start_search_input(forward=True)
        state.input_buffer = "/users/{id}"
        state = commit_search(state, self.entries, self.roots)

        self.assertTrue(users_node.expanded)
        self.assertIn("/users", state.temporary_expanded)

    def test_exit_search_recollapses_temporary_expansions(self):
        state = start_search_input(forward=True)
        state.input_buffer = "/users/{id}"
        state = commit_search(state, self.entries, self.roots)
        users_node = find_node_by_path(self.roots, "/users")
        self.assertTrue(users_node.expanded)

        state = exit_search(state, self.roots)
        self.assertFalse(state.active)
        self.assertFalse(users_node.expanded)
        self.assertFalse(state.temporary_expanded)

    def test_toggle_case_sensitivity_changes_matches(self):
        state = SearchState(
            active=True,
            direction=SearchDirection.FORWARD,
            pattern="get /users",
            case_sensitive=False,
        )
        insensitive = find_search_matches(
            self.entries,
            state.pattern,
            case_sensitive=state.case_sensitive,
        )
        state = toggle_search_case_sensitivity(state)
        sensitive = find_search_matches(
            self.entries,
            state.pattern,
            case_sensitive=state.case_sensitive,
        )
        self.assertGreater(len(insensitive), 0)
        self.assertEqual(len(sensitive), 0)

    def test_flatten_visible_entries_respects_expansion(self):
        visible_before = flatten_visible_entries(self.roots)
        self.assertFalse(any(entry.path == "/users/{id}" for entry in visible_before))

        users_node = find_node_by_path(self.roots, "/users")
        users_node.expanded = True
        visible_after = flatten_visible_entries(self.roots)
        self.assertTrue(any(entry.path == "/users/{id}" for entry in visible_after))
        users_node.expanded = False

    def test_clear_temporary_expansions_only_affects_tracked_paths(self):
        users_node = find_node_by_path(self.roots, "/users")
        users_node.expanded = True
        clear_temporary_expansions(self.roots, {"/users"})
        self.assertFalse(users_node.expanded)

    def test_apply_search_expansions_replaces_previous_temporary_paths(self):
        first_entry = next(
            entry for entry in self.entries if entry.label == "/users/{id}"
        )
        second_entry = next(
            entry for entry in self.entries if entry.label.startswith("GET /dogs")
        )

        temporary = apply_search_expansions(
            self.roots,
            first_entry,
            previous_expansions=set(),
        )
        users_node = find_node_by_path(self.roots, "/users")
        self.assertTrue(users_node.expanded)

        apply_search_expansions(
            self.roots,
            second_entry,
            previous_expansions=temporary,
        )
        self.assertFalse(users_node.expanded)


class TestMatchListAlignment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        cls.petstore_spec = load_spec_from_file(
            os.path.join(fixtures_dir, "sample_petstore_user.json")
        )
        cls.dsl_spec = load_spec_from_file(
            os.path.join(fixtures_dir, "sample_dsl.json")
        )

    def test_rich_match_list_includes_space_between_method_and_path(self):
        matches = execute_query("DELETE /users/{id}", self.dsl_spec)
        rendered = format_match_results_text(matches, terminal_width=120)
        self.assertIn("DELETE /users/{id}", rendered.plain)
        self.assertNotIn("DELETE/users", rendered.plain)

    def test_params_column_aligns_for_varied_path_lengths(self):
        matches = execute_query("/user*", self.petstore_spec)
        self.assertGreaterEqual(len(matches), 2)

        lines = format_match_list_lines(matches)
        params_columns = [line.index("params:") for line in lines if "params:" in line]
        self.assertTrue(params_columns)
        self.assertEqual(len(set(params_columns)), 1)

    def test_header_width_matches_longest_method_path_segment(self):
        matches = execute_query("/user*", self.petstore_spec)
        expected_width = match_list_header_width(matches)
        self.assertEqual(
            expected_width,
            max(len(line[: line.index("params:")].rstrip()) - 2 for line in format_match_list_lines(matches)),
        )

    def test_params_column_index_helper(self):
        matches = execute_query("/user*", self.petstore_spec)
        lines = format_match_list_lines(matches)
        expected = params_column_index(matches)
        for line in lines:
            if "params:" in line:
                self.assertEqual(line.index("params:"), expected)

    def test_body_parameter_includes_schema_summary(self):
        post_user = next(
            endpoint
            for endpoint in self.petstore_spec.endpoints
            if endpoint.method == "POST" and endpoint.path == "/user"
        )
        params = format_params(post_user)
        self.assertIn("body (User object, 8 fields)", params)

    def test_array_body_parameter_summarizes_item_schema(self):
        post_list = next(
            endpoint
            for endpoint in self.petstore_spec.endpoints
            if endpoint.method == "POST" and endpoint.path == "/user/createWithList"
        )
        params = format_params(post_list)
        self.assertIn("body (User object, 8 fields)", params)

    def test_format_match_results_uses_shared_alignment(self):
        matches = execute_query("/user*", self.petstore_spec)
        output = format_match_results(matches)
        params_columns = [
            line.index("params:")
            for line in output.splitlines()
            if "params:" in line
        ]
        self.assertEqual(len(set(params_columns)), 1)

    def test_long_paths_wrap_without_breaking_shorter_line_alignment(self):
        short = next(
            endpoint
            for endpoint in self.petstore_spec.endpoints
            if endpoint.method == "POST" and endpoint.path == "/user"
        )
        long_path = next(
            endpoint
            for endpoint in self.petstore_spec.endpoints
            if endpoint.path == "/user/createWithList"
        )
        matches = [short, long_path]

        unwrapped_lines = format_match_list_lines(matches)
        unwrapped_columns = {
            line.index("params:")
            for line in unwrapped_lines
            if "params:" in line
        }
        # Narrow enough to wrap "/user/createWithList" but not "[1] POST /user".
        wrapped_lines = format_match_list_lines(matches, terminal_width=25)
        wrapped_columns = {
            line.index("params:")
            for line in wrapped_lines
            if "params:" in line
        }

        self.assertEqual(len(unwrapped_columns), 1)
        self.assertEqual(len(wrapped_columns), 1)
        self.assertEqual(unwrapped_columns, wrapped_columns)
        self.assertGreater(len(wrapped_lines), len(unwrapped_lines))


class TestCleanCurlOutput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        cls.dsl_spec = load_spec_from_file(
            os.path.join(fixtures_dir, "sample_dsl.json")
        )
        cls.petstore_spec = load_spec_from_file(
            os.path.join(fixtures_dir, "sample_petstore_user.json")
        )
        cls.get_user_endpoint = next(
            endpoint
            for endpoint in cls.dsl_spec.endpoints
            if endpoint.method == "GET" and endpoint.path == "/users/{id}"
        )
        cls.post_user_list_endpoint = next(
            endpoint
            for endpoint in cls.petstore_spec.endpoints
            if endpoint.method == "POST" and endpoint.path == "/user/createWithList"
        )

    def test_build_endpoint_curl_command_returns_raw_curl(self):
        curl = build_endpoint_curl_command(self.get_user_endpoint, self.dsl_spec)
        self.assertTrue(curl.startswith("curl -X GET "))
        self.assertIn("Accept: application/json", curl)
        self.assertFalse(any(char in curl for char in "├│└"))

    def test_format_endpoint_detail_output_uses_single_blank_line(self):
        output = format_endpoint_detail_output("Parameters\n  id", "curl -X GET 'url'")
        self.assertEqual(output, "Parameters\n  id\n\ncurl -X GET 'url'")
        self.assertNotIn("\n\n\n", output)

    def test_render_endpoint_detail_output_separates_tree_and_curl(self):
        output = render_endpoint_detail_output(
            self.get_user_endpoint,
            self.dsl_spec,
            width=120,
        )
        self.assertIn("Parameters", output)
        self.assertIn("curl -X GET", output)
        self.assertNotIn("curl command:", output)

        curl_block = extract_curl_block_from_detail_output(output)
        self.assertTrue(curl_block_is_copy_pasteable(curl_block))
        self.assertIn("-H 'Accept: application/json'", curl_block)

    def test_curl_block_has_no_tree_prefix_characters(self):
        output = render_endpoint_detail_output(
            self.post_user_list_endpoint,
            self.petstore_spec,
            width=120,
        )
        curl_block = extract_curl_block_from_detail_output(output)
        self.assertTrue(curl_block_is_copy_pasteable(curl_block))
        self.assertIn("-d '", curl_block)
        for line in curl_block.splitlines():
            self.assertFalse(line.startswith(("├──", "│", "└──")))

    def test_curl_block_has_no_header_labels(self):
        output = render_endpoint_detail_output(
            self.get_user_endpoint,
            self.dsl_spec,
            width=120,
        )
        curl_block = extract_curl_block_from_detail_output(output)
        self.assertNotIn("--- curl ---", curl_block.lower())
        self.assertNotIn("curl command:", curl_block.lower())


if __name__ == "__main__":
    unittest.main()
