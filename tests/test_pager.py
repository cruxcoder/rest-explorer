"""Unit tests for the full-screen pager engine."""

import io
import os
import re
import unittest
from unittest.mock import patch

from rich.console import Console

from restx.cli.tree_view import KEY_DOWN, KEY_UP
from restx.cli.viewer import (
    KEY_CTRL_B,
    KEY_CTRL_F,
    KEY_PAGE_DOWN,
    KEY_PAGE_UP,
    KEY_SPACE,
    _format_match_list_pager_line,
    _format_pager_line,
    build_match_list_pager_buffer,
    collect_pager_display_state,
    display_match_results,
    handle_pager_navigation_key,
    match_results_need_pager,
    run_buffer_pager_session,
    run_pager_session,
)
from restx.core import execute_query, load_spec_from_file
from restx.core.pager import (
    Pager,
    PagerSearchDirection,
    find_pager_search_matches,
    render_api_tree_lines,
)
from restx.core.viewer import build_api_tree

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_dsl_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
    return load_spec_from_file(path)


class TestPagerRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()
        cls.roots = build_api_tree(cls.spec)

    def test_render_api_tree_lines_includes_paths_and_endpoints(self):
        lines = render_api_tree_lines(self.roots)
        joined = "\n".join(lines)
        self.assertIn("/users", joined)
        self.assertIn("GET", joined)
        self.assertIn("POST", joined)

    def test_render_api_tree_lines_aligns_method_and_path_columns(self):
        HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}

        def is_endpoint_line(line: str) -> bool:
            return bool(line.strip()) and line.lstrip().split()[0] in HTTP_METHODS

        def sibling_endpoint_groups(buffer: list[str]) -> list[list[str]]:
            groups: list[list[str]] = []
            index = 0
            while index < len(buffer):
                line = buffer[index]
                if not line.strip() or is_endpoint_line(line):
                    index += 1
                    continue
                path_prefix_len = len(line) - len(line.lstrip(" "))
                endpoint_prefix_len = path_prefix_len + 2
                index += 1
                group: list[str] = []
                while index < len(buffer):
                    next_line = buffer[index]
                    if not next_line.strip():
                        break
                    next_prefix_len = len(next_line) - len(next_line.lstrip(" "))
                    if next_prefix_len != endpoint_prefix_len or not is_endpoint_line(next_line):
                        break
                    group.append(next_line)
                    index += 1
                if group:
                    groups.append(group)
            return groups

        lines = render_api_tree_lines(self.roots)
        groups = sibling_endpoint_groups(lines)
        self.assertGreaterEqual(len(groups), 2)

        group_method_widths: list[int] = []
        for group in groups:
            methods = [line.lstrip().split()[0] for line in group]
            method_width = max(len(method) for method in methods)
            group_method_widths.append(method_width)
            path_starts: set[int] = set()
            for line in group:
                prefix_len = len(line) - len(line.lstrip(" "))
                body = line[prefix_len:]
                method_part = body[:method_width]
                self.assertIn(method_part.strip(), HTTP_METHODS)
                self.assertEqual(body[method_width], " ")
                path_starts.add(prefix_len + method_width + 1)
            self.assertEqual(
                len(path_starts),
                1,
                f"paths misaligned within sibling group: {path_starts}",
            )

        self.assertIn(4, group_method_widths)
        self.assertIn(6, group_method_widths)

        users_id_group = next(
            group for group in groups if group[0].rstrip().endswith("/users/{id}")
        )
        self.assertEqual(
            users_id_group,
            [
                "    DELETE /users/{id}",
                "    GET    /users/{id}",
                "    PUT    /users/{id}",
            ],
        )

        users_group = next(
            group for group in groups if group[0].rstrip().endswith("/users")
        )
        self.assertEqual(
            users_group,
            [
                "  GET  /users",
                "  POST /users",
            ],
        )

        summary_spec = load_spec_from_file(
            os.path.join(FIXTURES_DIR, "sample_openapi_30.json")
        )
        summary_roots = build_api_tree(summary_spec)
        summary_lines = render_api_tree_lines(summary_roots)
        summary_groups = sibling_endpoint_groups(summary_lines)
        summary_with_dash = [
            group
            for group in summary_groups
            if group and " — " in group[0]
        ]
        self.assertGreaterEqual(len(summary_with_dash), 1)
        for group in summary_with_dash:
            methods = [line.lstrip().split()[0] for line in group]
            method_width = max(len(method) for method in methods)
            for line in group:
                prefix_len = len(line) - len(line.lstrip(" "))
                body = line[prefix_len:]
                dash_index = body.index(" — ")
                path = body[method_width + 1 : dash_index]
                self.assertEqual(dash_index, method_width + 1 + len(path))

        root_path_lines = [
            line for line in lines if line and not line.startswith(" ")
        ]
        self.assertGreaterEqual(len(root_path_lines), 2)
        blank_before_roots = 0
        for index, line in enumerate(lines):
            if line in root_path_lines[1:] and index > 0:
                self.assertEqual(lines[index - 1], "")
                blank_before_roots += 1
        self.assertEqual(blank_before_roots, len(root_path_lines) - 1)

    def test_format_pager_line_preserves_per_group_method_padding(self):
        aligned = "    GET    /users/{id}"
        formatted = _format_pager_line(aligned)
        plain = re.sub(r"\[[^\]]*\]", "", formatted)
        self.assertEqual(plain, aligned)

    def test_pager_builds_buffer_from_spec(self):
        pager = Pager(self.spec, page_height=10)
        self.assertGreater(pager.total_lines, 0)
        self.assertEqual(len(pager.buffer), pager.total_lines)

    def test_pager_from_lines_uses_prebuilt_buffer(self):
        lines = ["  2 matches:", "  [1] GET  /users", "  [2] POST /users"]
        pager = Pager.from_lines(lines, page_height=2)
        self.assertEqual(pager.buffer, lines)
        self.assertEqual(pager.total_lines, 3)
        self.assertLessEqual(len(pager.visible_lines()), 2)


class TestPagerPagination(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_visible_lines_respect_page_height(self):
        pager = Pager(self.spec, page_height=3)
        self.assertLessEqual(len(pager.visible_lines()), 3)

    def test_scroll_down_and_up(self):
        pager = Pager(self.spec, page_height=2)
        self.assertTrue(pager.at_top())
        pager.scroll_down(1)
        self.assertEqual(pager.scroll_offset, 1)
        pager.scroll_up(1)
        self.assertTrue(pager.at_top())

    def test_scroll_page_down_reaches_bottom(self):
        pager = Pager(self.spec, page_height=2)
        pager.scroll_page_down()
        while not pager.at_bottom():
            pager.scroll_page_down()
        self.assertTrue(pager.at_bottom())
        self.assertLessEqual(len(pager.visible_lines()), 2)

    def test_scroll_half_page_moves_by_half_viewport(self):
        pager = Pager(self.spec, page_height=10)
        pager.scroll_half_page_down()
        self.assertEqual(pager.scroll_offset, pager.half_page_lines)
        pager.scroll_half_page_up()
        self.assertTrue(pager.at_top())

    def test_half_page_lines_is_at_least_one(self):
        pager = Pager(self.spec, page_height=1)
        self.assertEqual(pager.half_page_lines, 1)

    def test_scroll_offset_clamped_to_buffer(self):
        pager = Pager(self.spec, page_height=1000)
        self.assertEqual(pager.scroll_offset, 0)
        self.assertTrue(pager.at_top())
        self.assertTrue(pager.at_bottom())


class TestPagerFiltering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_filter_pattern_limits_buffer(self):
        full = Pager(self.spec, page_height=1000)
        filtered = Pager(self.spec, page_height=1000, filter_pattern="/users*")
        self.assertLess(filtered.total_lines, full.total_lines)
        joined = "\n".join(filtered.buffer)
        self.assertIn("/users", joined)
        self.assertNotIn("/dogs", joined)


class TestPagerSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_find_pager_search_matches(self):
        pager = Pager(self.spec, page_height=1000)
        matches = find_pager_search_matches(pager.buffer, "GET /users")
        self.assertTrue(matches)
        for index in matches:
            line = pager.buffer[index]
            self.assertIn("GET", line)
            self.assertIn("/users", line)

    def test_forward_search_highlights_and_scrolls(self):
        pager = Pager(self.spec, page_height=2)
        pager.start_search(forward=True)
        pager.set_search_pattern("POST")
        self.assertTrue(pager.search.active)
        self.assertTrue(pager.search.match_line_indices)
        self.assertEqual(pager.search.direction, PagerSearchDirection.FORWARD)
        current = pager.current_match_line()
        self.assertIsNotNone(current)
        self.assertIn(current, pager.highlighted_line_indices())

    def test_backward_search_starts_at_last_match(self):
        pager = Pager(self.spec, page_height=1000)
        pager.start_search(forward=False)
        pager.set_search_pattern("GET")
        self.assertEqual(
            pager.search.current_match,
            len(pager.search.match_line_indices) - 1,
        )

    def test_search_next_and_previous_cycle(self):
        pager = Pager(self.spec, page_height=1000)
        pager.set_search_pattern("GET")
        first = pager.search.current_match
        pager.search_next()
        if len(pager.search.match_line_indices) > 1:
            self.assertNotEqual(pager.search.current_match, first)
        pager.search_previous()
        self.assertEqual(pager.search.current_match, first)

    def test_clear_search_resets_state(self):
        pager = Pager(self.spec, page_height=10)
        pager.set_search_pattern("users")
        pager.clear_search()
        self.assertFalse(pager.search.active)
        self.assertEqual(pager.highlighted_line_indices(), set())


class TestPagerNavigationKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_handle_pager_navigation_key_arrow_and_page_keys(self):
        pager = Pager(self.spec, page_height=4)
        self.assertTrue(handle_pager_navigation_key(pager, KEY_DOWN))
        self.assertEqual(pager.scroll_offset, 1)
        self.assertTrue(handle_pager_navigation_key(pager, KEY_UP))
        self.assertTrue(pager.at_top())
        self.assertTrue(handle_pager_navigation_key(pager, KEY_PAGE_DOWN))
        self.assertEqual(pager.scroll_offset, 4)
        self.assertTrue(handle_pager_navigation_key(pager, KEY_PAGE_UP))
        self.assertTrue(pager.at_top())

    def test_handle_pager_navigation_key_ctrl_b_and_ctrl_f(self):
        pager = Pager(self.spec, page_height=10)
        self.assertTrue(handle_pager_navigation_key(pager, KEY_CTRL_F))
        self.assertEqual(pager.scroll_offset, pager.max_scroll_offset)
        self.assertTrue(pager.at_bottom())
        self.assertTrue(handle_pager_navigation_key(pager, KEY_CTRL_B))
        self.assertTrue(pager.at_top())

    def test_handle_pager_navigation_key_space_pages_down(self):
        pager = Pager(self.spec, page_height=4)
        self.assertTrue(handle_pager_navigation_key(pager, KEY_SPACE))
        self.assertEqual(pager.scroll_offset, 4)
        self.assertTrue(handle_pager_navigation_key(pager, KEY_CTRL_B))
        self.assertTrue(pager.at_top())

    def test_unknown_key_not_handled(self):
        pager = Pager(self.spec, page_height=4)
        offset = pager.scroll_offset
        self.assertFalse(handle_pager_navigation_key(pager, "q"))
        self.assertEqual(pager.scroll_offset, offset)


class TestPagerDeltaRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_collect_pager_display_state_tracks_scroll(self):
        pager = Pager(self.spec, page_height=3)
        before = collect_pager_display_state(pager)
        pager.scroll_down()
        after = collect_pager_display_state(pager)
        self.assertNotEqual(before.scroll_offset, after.scroll_offset)
        self.assertNotEqual(before.content_lines, after.content_lines)

    @patch("restx.cli.viewer._pager_page_height", return_value=5)
    @patch("restx.cli.viewer._read_key", side_effect=[KEY_DOWN, "q"])
    @patch("restx.cli.viewer.sys.stdin.isatty", return_value=True)
    @patch("restx.cli.viewer.sys.stdout", new_callable=io.StringIO)
    def test_pager_session_uses_single_clear_on_scroll(
        self,
        mock_stdout,
        _mock_isatty,
        _mock_read_key,
        _mock_page_height,
    ):
        console = Console(force_terminal=False, no_color=True, width=80)
        run_pager_session(self.spec, console)
        combined = mock_stdout.getvalue()
        self.assertEqual(combined.count("\x1b[2J"), 0)
        self.assertIn("\x1b[1;1H", combined)
        self.assertIn("\x1b[S", combined)

    @patch("restx.cli.viewer._read_key", side_effect=[KEY_PAGE_DOWN, KEY_PAGE_UP, "q"])
    @patch("restx.cli.viewer.sys.stdin.isatty", return_value=True)
    @patch("restx.cli.viewer.sys.stdout", new_callable=io.StringIO)
    def test_pager_session_page_up_and_page_down(
        self,
        _mock_stdout,
        _mock_isatty,
        _mock_read_key,
    ):
        pager = Pager(self.spec, page_height=4)
        handle_pager_navigation_key(pager, KEY_PAGE_DOWN)
        self.assertEqual(pager.scroll_offset, 4)
        handle_pager_navigation_key(pager, KEY_PAGE_UP)
        self.assertTrue(pager.at_top())


class TestMatchListPager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_format_match_list_pager_line_escapes_index_brackets(self):
        line = "  [655] GET  /users params: email"
        formatted = _format_match_list_pager_line(line)
        console = Console(force_terminal=False, no_color=True, width=120)
        with console.capture() as capture:
            console.print(formatted, highlight=False, end="")
        rendered = capture.get()
        self.assertIn("[655]", rendered)
        self.assertIn("GET", rendered)
        self.assertIn("/users", rendered)

    def test_build_match_list_pager_buffer_includes_header_and_lines(self):
        matches = execute_query("GET /users*", self.spec)
        buffer = build_match_list_pager_buffer(matches)
        self.assertTrue(buffer[0].endswith("matches:"))
        self.assertGreaterEqual(len(buffer), len(matches) + 1)

    @patch("restx.cli.viewer.sys.stdout.isatty", return_value=True)
    @patch("restx.cli.viewer._pager_page_height", return_value=3)
    def test_match_results_need_pager_when_buffer_exceeds_viewport(
        self,
        _mock_height,
        _mock_isatty,
    ):
        matches = execute_query("GET", self.spec)
        self.assertGreater(len(build_match_list_pager_buffer(matches)), 3)
        self.assertTrue(
            match_results_need_pager(matches, terminal_width=80, page_height=3)
        )

    @patch("restx.cli.viewer.sys.stdout.isatty", return_value=True)
    @patch("restx.cli.viewer._pager_page_height", return_value=1000)
    def test_match_results_skip_pager_when_buffer_fits(
        self,
        _mock_height,
        _mock_isatty,
    ):
        matches = execute_query("GET /users", self.spec)
        self.assertFalse(
            match_results_need_pager(matches, terminal_width=80, page_height=1000)
        )

    @patch("restx.cli.viewer.sys.stdout.isatty", return_value=True)
    @patch("restx.cli.viewer.run_match_list_pager_session")
    @patch("restx.cli.viewer.match_results_need_pager", return_value=True)
    def test_display_match_results_routes_through_pager(
        self,
        _mock_need_pager,
        mock_run_pager,
        _mock_isatty,
    ):
        matches = execute_query("GET /users*", self.spec)
        console = Console(force_terminal=False, no_color=True, width=80)
        display_match_results(console, matches)
        mock_run_pager.assert_called_once_with(matches, console)

    @patch("restx.cli.viewer.sys.stdout.isatty", return_value=True)
    @patch("restx.cli.viewer.match_results_need_pager", return_value=False)
    def test_display_match_results_prints_directly_when_short(
        self,
        _mock_need_pager,
        _mock_isatty,
    ):
        matches = execute_query("GET /users", self.spec)
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=False,
            no_color=True,
            width=80,
        )
        display_match_results(console, matches)
        output = buffer.getvalue()
        self.assertIn("1 match", output)
        self.assertIn("[1] GET", output)

    @patch("restx.cli.viewer._read_key", side_effect=[KEY_SPACE, "q"])
    @patch("restx.cli.viewer.sys.stdin.isatty", return_value=True)
    @patch("restx.cli.viewer.sys.stdout", new_callable=io.StringIO)
    def test_match_list_pager_session_space_pages_down(
        self,
        _mock_stdout,
        _mock_isatty,
        _mock_read_key,
    ):
        matches = execute_query("GET", self.spec)
        console = Console(force_terminal=False, no_color=True, width=80)
        buffer = build_match_list_pager_buffer(matches)
        self.assertGreater(len(buffer), 3)
        pager = Pager.from_lines(buffer, page_height=3)
        run_buffer_pager_session(
            pager,
            console,
            format_line=lambda line, **kwargs: line,
            title="Match results",
        )
        self.assertEqual(pager.scroll_offset, 3)


if __name__ == "__main__":
    unittest.main()
