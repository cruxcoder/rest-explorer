"""Unit tests for REPL history, color detection, and error message formatting."""

import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from restx.cli.colors import (
    METHOD_STYLES,
    PATH_STYLE,
    STATUS_LINE_ANSI,
    STATUS_LINE_FG,
    STATUS_LINE_SEPARATOR_STYLE,
    build_repl_status_toolbar,
    resolve_colors_enabled,
    wrap_status_line_ansi,
)
from restx.cli.repl import (
    LoadingSpinner,
    PromptHintState,
    format_repl_launch_summary,
    make_spec_loader_spinner_callback,
    normalize_spec_source,
    pad_prompt_to_bottom,
    prepare_repl_launch,
)
from restx.cli.viewer import (
    build_repl_bottom_toolbar,
    format_repl_status_line,
    render_repl_status_line,
    repl_interaction_mode,
    truncate_source_middle,
)
from restx.cli.history import (
    DEFAULT_HISTORY_MAX,
    BoundedFileHistory,
    create_history,
    history_max_entries,
)
from restx.cli.tree_view import parse_selection
from restx.cli.viewer import format_detail_view_header, run_repl_detail_view
from restx.core import (
    QueryContext,
    QueryParseError,
    SpecLoadError,
    SpecParseError,
    UnsupportedSpecVersionError,
    execute_query,
    load_spec_from_file,
    load_spec_from_url,
)
from restx.core.dsl_parser import parse_query
from restx.core.matcher import ZERO_MATCH_MESSAGE

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_dsl_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
    return load_spec_from_file(path)


class TestNonDestructiveSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_query_context_stores_match_results(self):
        context = QueryContext()
        matches = execute_query("GET /users*", self.spec)
        context.match_results.set_matches(matches)

        self.assertTrue(context.match_results.has_matches)
        self.assertEqual(context.match_results.match_count, len(matches))
        self.assertIsNone(context.match_results.selected_index)

    def test_numeric_input_selects_from_active_result_set(self):
        context = QueryContext()
        matches = execute_query("GET /users*", self.spec)
        context.match_results.set_matches(matches)

        selection_index = parse_selection("2")
        self.assertEqual(selection_index, 2)

        endpoint = context.match_results.select(selection_index)
        self.assertIsNotNone(endpoint)
        self.assertEqual(endpoint.path, matches[1].path)
        self.assertTrue(context.match_results.detail_open)

    def test_switch_selection_without_rerunning_search(self):
        context = QueryContext()
        matches = execute_query("GET /users*", self.spec)
        context.match_results.set_matches(matches)

        first = context.match_results.select(1)
        context.match_results.close_detail()
        second = context.match_results.select(2)

        self.assertEqual(context.match_results.match_count, len(matches))
        self.assertNotEqual(first.path, second.path)

    def test_detail_view_header_format(self):
        matches = execute_query("GET /users/{id}", self.spec)
        header = format_detail_view_header(1, matches[0])
        self.assertEqual(
            header,
            f"--- Detail view for [1] {matches[0].method} {matches[0].path} ---",
        )

    @patch("restx.cli.viewer.run_interactive_tree")
    def test_repl_detail_view_preserves_match_list(self, mock_run_tree):
        matches = execute_query("GET /users/{id}", self.spec)
        endpoint = matches[0]
        console = MagicMock()

        run_repl_detail_view(1, endpoint, self.spec, console)

        console.print.assert_any_call(format_detail_view_header(1, endpoint))
        mock_run_tree.assert_called_once()
        self.assertFalse(mock_run_tree.call_args.kwargs["clear_screen"])

    @patch("restx.cli.tree_view._read_key", return_value="q")
    @patch("restx.cli.tree_view.sys.stdin.isatty", return_value=True)
    @patch("restx.cli.tree_view.Console.clear")
    def test_detail_view_q_returns_without_clearing_screen(
        self,
        mock_clear,
        _mock_isatty,
        _mock_read_key,
    ):
        from restx.cli.tree_view import build_endpoint_tree, run_interactive_tree

        matches = execute_query("GET /users/{id}", self.spec)
        endpoint = matches[0]
        root = build_endpoint_tree(endpoint, self.spec)

        run_interactive_tree(
            root,
            f"{endpoint.method} {endpoint.path}",
            curl_command="curl -X GET 'https://example.com/users/1'",
            clear_screen=False,
        )

        mock_clear.assert_not_called()


class TestLoadingSpinner(unittest.TestCase):
    def test_spinner_callback_phases_during_file_load(self):
        events: list[str] = []

        def spinner(phase: str) -> None:
            events.append(phase)

        path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
        load_spec_from_file(path, spinner=spinner)

        self.assertEqual(events, ["parse", "done"])

    def test_spinner_callback_phases_during_url_load(self):
        events: list[str] = []

        def spinner(phase: str) -> None:
            events.append(phase)

        path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
        mock_response = MagicMock()
        mock_response.text = Path(path).read_text(encoding="utf-8")
        mock_response.raise_for_status = MagicMock()

        with patch(
            "restx.core.spec_loader.requests.get",
            return_value=mock_response,
        ):
            load_spec_from_url(
                "https://example.com/spec.json",
                spinner=spinner,
            )

        self.assertEqual(events, ["fetch", "parse", "done"])

    def test_spinner_callback_done_on_fetch_error(self):
        events: list[str] = []

        def spinner(phase: str) -> None:
            events.append(phase)

        with patch(
            "restx.core.spec_loader.requests.get",
            side_effect=__import__("requests").exceptions.Timeout(),
        ):
            with self.assertRaises(SpecLoadError):
                load_spec_from_url(
                    "https://example.com/spec.json",
                    spinner=spinner,
                )

        self.assertEqual(events, ["fetch", "done"])

    def test_loading_spinner_ticks_and_clears_in_place(self):
        stream = io.StringIO()
        spinner = LoadingSpinner(stream=stream, interval=0.01)

        spinner.start()
        time.sleep(0.05)
        output = stream.getvalue()
        self.assertTrue(output.startswith("\rLoading spec... "))
        self.assertGreaterEqual(output.count("\rLoading spec..."), 2)

        spinner.clear()
        cleared = stream.getvalue()
        self.assertTrue(cleared.endswith("\r"))

    def test_make_spec_loader_spinner_callback_clears_on_done(self):
        stream = io.StringIO()
        callback = make_spec_loader_spinner_callback(
            LoadingSpinner(stream=stream, interval=0.01)
        )

        callback("fetch")
        time.sleep(0.05)
        self.assertIn("Loading spec...", stream.getvalue())

        callback("done")
        self.assertTrue(stream.getvalue().endswith("\r"))

    def test_spinner_animates_during_slow_url_fetch(self):
        stream = io.StringIO()
        callback = make_spec_loader_spinner_callback(
            LoadingSpinner(stream=stream, interval=0.01)
        )
        path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
        mock_response = MagicMock()
        mock_response.text = Path(path).read_text(encoding="utf-8")
        mock_response.raise_for_status = MagicMock()

        def slow_get(*_args, **_kwargs):
            time.sleep(0.12)
            return mock_response

        with patch(
            "restx.core.spec_loader.requests.get",
            side_effect=slow_get,
        ):
            load_spec_from_url(
                "https://example.com/spec.json",
                spinner=callback,
            )

        self.assertGreaterEqual(stream.getvalue().count("\rLoading spec..."), 3)


class TestReplStatusLine(unittest.TestCase):
    def test_truncate_source_middle(self):
        source = "https://petstore.swagger.io/v2/swagger.json"
        truncated = truncate_source_middle(source, 35)

        self.assertLessEqual(len(truncated), 35)
        self.assertIn("...", truncated)
        self.assertTrue(truncated.startswith("https://pet"))
        self.assertTrue(truncated.endswith("swagger.json"))

    def test_format_repl_status_line(self):
        line = format_repl_status_line(
            "https://petstore.swagger.io/v2/swagger.json",
            "Interactive",
        )
        self.assertEqual(
            line,
            "API: https://petstore.swagger.io/v2/swagger.json | Mode: Interactive",
        )

    def test_format_repl_status_line_truncates_long_url(self):
        source = "https://petstore.swagger.io/v2/swagger.json"
        line = format_repl_status_line(source, "Interactive", terminal_width=40)

        self.assertIn("API:", line)
        self.assertIn("| Mode: Interactive", line)
        self.assertIn("...", line)
        self.assertLess(len(line), len(source) + len("API:  | Mode: Interactive"))

    def test_render_repl_status_line_matches_format(self):
        source = "./local-spec.yaml"
        self.assertEqual(
            render_repl_status_line(source, "Context"),
            format_repl_status_line(source, "Context"),
        )

    def test_repl_interaction_mode_labels(self):
        self.assertEqual(
            repl_interaction_mode(context_enabled=False),
            "Interactive",
        )
        self.assertEqual(
            repl_interaction_mode(context_enabled=True),
            "Context",
        )
        self.assertEqual(
            repl_interaction_mode(context_enabled=False, browsing=True),
            "Browse",
        )

    def test_status_line_uses_transparent_styling(self):
        from prompt_toolkit.formatted_text import to_formatted_text

        line = "API: ./spec.json | Mode: Interactive"
        toolbar = build_repl_status_toolbar(line, terminal_width=40)
        parts = to_formatted_text(toolbar)
        text = "".join(part[1] for part in parts if len(part) > 1)
        styles = [part[0] for part in parts if len(part) > 1]

        self.assertIn("API:", text)
        self.assertIn("| Mode: Interactive", text)
        self.assertIn("─", text)
        self.assertTrue(styles)
        self.assertTrue(any(STATUS_LINE_FG in style for style in styles))
        self.assertFalse(any("bg:" in style for style in styles if style))

    def test_build_repl_bottom_toolbar_uses_transparent_status(self):
        from prompt_toolkit.formatted_text import to_formatted_text

        toolbar = build_repl_bottom_toolbar(
            "./spec.json",
            "Interactive",
            terminal_width=40,
            color_enabled=True,
        )
        parts = to_formatted_text(toolbar)
        styles = [part[0] for part in parts if len(part) > 1]
        self.assertTrue(any(STATUS_LINE_FG in style for style in styles))
        self.assertFalse(any("bg:" in style for style in styles if style))

    def test_wrap_status_line_ansi_uses_foreground_only(self):
        wrapped = wrap_status_line_ansi("API: test | Mode: Context")
        self.assertTrue(wrapped.startswith(STATUS_LINE_ANSI))
        self.assertTrue(wrapped.endswith("\x1b[0m"))
        self.assertIn("| Mode: Context", wrapped)
        self.assertNotIn("48;", wrapped)

    def test_method_styles_use_vibrant_light_theme_colors(self):
        self.assertEqual(METHOD_STYLES["GET"], "dark_green")
        self.assertEqual(METHOD_STYLES["POST"], "gold1")
        self.assertEqual(PATH_STYLE, "dark_cyan")

    def test_normalize_spec_source(self):
        self.assertEqual(normalize_spec_source(None), "stdin")
        self.assertEqual(
            normalize_spec_source("https://example.com/spec.json"),
            "https://example.com/spec.json",
        )

    def test_format_repl_launch_summary(self):
        path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
        spec = load_spec_from_file(path)
        summary = format_repl_launch_summary(spec, path)

        self.assertIn("Loaded spec:", summary)
        self.assertIn("endpoints", summary)
        self.assertIn("paths", summary)
        self.assertIn("server", summary)
        self.assertIn("Auth:", summary)


class TestPromptHintState(unittest.TestCase):
    def test_hint_visible_on_startup(self):
        state = PromptHintState()
        self.assertIsNotNone(state.placeholder())

    def test_keystroke_hides_hint_immediately(self):
        state = PromptHintState()
        state.mark_keystroke()
        self.assertIsNone(state.placeholder())

    def test_submit_hides_hint(self):
        state = PromptHintState()
        state.mark_submitted()
        self.assertIsNone(state.placeholder())

    def test_idle_trigger_restores_hint(self):
        state = PromptHintState()
        state.mark_submitted()
        state.mark_idle()
        self.assertIsNotNone(state.placeholder())

    def test_idle_after_keystroke_clears_typed_flag(self):
        state = PromptHintState()
        state.mark_keystroke()
        state.mark_idle()
        self.assertIsNotNone(state.placeholder())

    def test_query_does_not_restore_hint_without_idle(self):
        state = PromptHintState()
        state.mark_submitted()
        self.assertIsNone(state.placeholder())


class TestPromptPositioning(unittest.TestCase):
    @patch("restx.cli.repl._terminal_rows", return_value=24)
    def test_pad_prompt_to_bottom(self, _mock_rows):
        stream = io.StringIO()

        def isatty() -> bool:
            return True

        stream.isatty = isatty  # type: ignore[attr-defined]
        with patch("restx.cli.repl.sys.stdout", stream):
            pad_prompt_to_bottom()
        self.assertEqual(stream.getvalue().count("\n"), 21)

    @patch("restx.cli.repl.sys.stdout.isatty", return_value=True)
    def test_prepare_repl_launch_prints_loaded_spec_summary(
        self,
        _mock_isatty,
    ):
        path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
        spec = load_spec_from_file(path)
        console = MagicMock()

        prepare_repl_launch(console, spec, path)

        console.print.assert_called_once()
        message = console.print.call_args[0][0]
        self.assertIn("Loaded spec:", message)
        self.assertIn("Auth:", message)


class TestHistoryPersistence(unittest.TestCase):
    def test_history_file_created_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "restx_history"
            history = create_history(history_path, max_entries=100)

            history.append_string("GET /users*")
            history.append_string("req:email")

            lines = history_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines, ["GET /users*", "req:email"])

    def test_history_max_entry_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "restx_history"
            history = BoundedFileHistory(history_path, max_entries=3)

            for index in range(5):
                history.append_string(f"query-{index}")

            lines = history_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines, ["query-2", "query-3", "query-4"])

    def test_history_max_entries_configurable_via_env(self):
        with patch.dict(os.environ, {"RESTX_HISTORY_MAX": "42"}):
            self.assertEqual(history_max_entries(), 42)

    def test_history_invalid_env_falls_back_to_default(self):
        with patch.dict(os.environ, {"RESTX_HISTORY_MAX": "not-a-number"}):
            self.assertEqual(history_max_entries(), DEFAULT_HISTORY_MAX)


class TestColorDetection(unittest.TestCase):
    def test_color_always(self):
        self.assertTrue(
            resolve_colors_enabled("always", no_color_env="1", is_tty=False)
        )

    def test_color_never(self):
        self.assertFalse(
            resolve_colors_enabled("never", no_color_env=None, is_tty=True)
        )

    def test_color_auto_respects_no_color(self):
        self.assertFalse(
            resolve_colors_enabled("auto", no_color_env="1", is_tty=True)
        )

    def test_color_auto_enables_on_tty(self):
        self.assertTrue(
            resolve_colors_enabled("auto", no_color_env=None, is_tty=True)
        )

    def test_color_auto_disables_off_tty(self):
        self.assertFalse(
            resolve_colors_enabled("auto", no_color_env=None, is_tty=False)
        )


class TestErrorMessageFormats(unittest.TestCase):
    def test_malformed_yaml_message(self):
        path = os.path.join(FIXTURES_DIR, "sample_malformed.yaml")
        with self.assertRaises(SpecParseError) as ctx:
            load_spec_from_file(path)

        message = str(ctx.exception)
        self.assertIn("Failed to parse spec: YAML error at line", message)
        self.assertIn("Verify the file is valid YAML", message)

    def test_unsupported_version_message(self):
        path = os.path.join(FIXTURES_DIR, "sample_swagger_12.json")
        with self.assertRaises(UnsupportedSpecVersionError) as ctx:
            load_spec_from_file(path)

        self.assertEqual(
            str(ctx.exception),
            "Unsupported spec version: 1.2. RestX supports Swagger 2.0 and OpenAPI 3.0.x–3.2.x.",
        )

    def test_invalid_query_syntax_message(self):
        with self.assertRaises(QueryParseError) as ctx:
            parse_query("req::email")

        self.assertIn(
            "Parse error near 'req::' — unexpected ':'. Did you mean 'req:email'?",
            str(ctx.exception),
        )

    def test_zero_matches_message(self):
        path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
        spec = load_spec_from_file(path)
        matches = execute_query("DELETE /nonexistent*", spec)
        self.assertEqual(matches, [])
        self.assertIn("No matches", ZERO_MATCH_MESSAGE)

    def test_file_not_found_message(self):
        missing = "./my-spec.yaml"
        with self.assertRaises(SpecLoadError) as ctx:
            load_spec_from_file(missing)

        self.assertEqual(
            str(ctx.exception),
            f"File not found: '{missing}'. Check the path and try again.",
        )

    def test_empty_stdin_message(self):
        import io
        import sys

        from restx.core import load_spec_from_stdin

        with patch.object(sys, "stdin", io.StringIO("")):
            with self.assertRaises(SpecLoadError) as ctx:
                load_spec_from_stdin()

        self.assertEqual(
            str(ctx.exception),
            "No input received on stdin. Pipe a spec file: cat spec.json | restx",
        )

    def test_network_timeout_message(self):
        from restx.core import load_spec_from_url

        with patch(
            "restx.core.spec_loader.requests.get",
            side_effect=__import__("requests").exceptions.Timeout(),
        ):
            with self.assertRaises(SpecLoadError) as ctx:
                load_spec_from_url("https://example.com/spec.json")

        message = str(ctx.exception)
        self.assertIn("Failed to fetch 'https://example.com/spec.json'", message)
        self.assertIn("Connection timed out", message)
        self.assertIn("Verify the URL is accessible", message)


if __name__ == "__main__":
    unittest.main()
