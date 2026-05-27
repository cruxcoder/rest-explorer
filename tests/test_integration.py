"""
Integration tests for RestX v1.3 features and public API loading.

These tests drive the REPL and pager end-to-end with mocked terminal and
subprocess dependencies so v1.3 behavior is verified without a live network
or interactive terminal.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from prompt_toolkit.formatted_text import to_formatted_text
from rich.console import Console

from restx.cli.colors import (
    METHOD_STYLES,
    PAGER_SELECTED_STYLE,
    SEARCH_HIGHLIGHT_STYLE,
    STATUS_LINE_ANSI,
    STATUS_LINE_FG,
    build_repl_status_toolbar,
    wrap_status_line_ansi,
)
from restx.cli.commands import SHELL_OUTPUT_HEADER, parse_repl_command
from restx.cli.repl import run_repl
from restx.cli.tree_view import KEY_DOWN, KEY_UP
from restx.cli.viewer import (
    _format_pager_line,
    _format_pager_status,
    format_repl_status_line,
    render_endpoint_markup,
    run_pager_session,
    truncate_source_middle,
)
from restx.core import QueryContext, execute_query, load_spec_from_file, load_spec_from_url

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
PETSTORE_URL = "https://petstore.swagger.io/v2/swagger.json"
NETWORK = unittest.skipUnless(
    os.environ.get("RESTX_RUN_NETWORK_TESTS") == "1",
    "Set RESTX_RUN_NETWORK_TESTS=1 to run network integration tests",
)


def _load_dsl_fixture():
    return load_spec_from_file(os.path.join(FIXTURES_DIR, "sample_dsl.json"))


def _load_curl_fixture():
    return load_spec_from_file(os.path.join(FIXTURES_DIR, "sample_curl.json"))


def _load_petstore_fixture():
    return load_spec_from_file(os.path.join(FIXTURES_DIR, "sample_petstore_user.json"))


def _iter_prompt_inputs(values: list[str]):
    """Yield scripted REPL inputs, then EOF to exit the loop."""
    for value in values:
        yield value
    while True:
        raise EOFError


def _run_repl_with_inputs(
    spec,
    inputs: list[str],
    *,
    color_mode: str = "never",
    spec_source: str = "./tests/fixtures/sample_dsl.json",
) -> str:
    """Drive ``run_repl`` with scripted input and return captured console output."""
    output = io.StringIO()
    console = Console(
        file=output,
        force_terminal=False,
        no_color=True,
        width=80,
        highlight=False,
    )
    mock_session = MagicMock()
    mock_session.prompt.side_effect = _iter_prompt_inputs(inputs)

    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = Path(tmpdir) / "restx_history"
        with patch("restx.cli.repl.PromptSession", return_value=mock_session), patch(
            "restx.cli.repl.create_console",
            return_value=console,
        ):
            run_repl(
                spec,
                color_mode=color_mode,
                spec_source=spec_source,
                history_path=history_path,
            )

    return output.getvalue()


@NETWORK
class TestPetstoreIntegration(unittest.TestCase):
    def test_fetch_and_parse_petstore_spec(self):
        spec = load_spec_from_url(PETSTORE_URL)

        self.assertEqual(spec.openapi_version, "2.0")
        self.assertGreater(spec.endpoint_count, 0)
        self.assertTrue(
            any(endpoint.path.startswith("/pet") for endpoint in spec.endpoints)
        )


class TestV13ShellIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    @patch("restx.core.shell.subprocess.run")
    def test_repl_inline_shell_executes_and_returns(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="file.txt\n",
            stderr="",
            returncode=0,
        )

        output = _run_repl_with_inputs(
            self.spec,
            ["! ls -ltr", ".quit"],
        )

        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs["shell"])
        self.assertTrue(kwargs["capture_output"])
        self.assertIn(SHELL_OUTPUT_HEADER, output)
        self.assertIn("file.txt", output)

    @patch("restx.core.shell.subprocess.run")
    def test_repl_inline_shell_shows_stderr(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="permission denied\n",
            returncode=1,
        )

        output = _run_repl_with_inputs(
            self.spec,
            ["! bad-command", ".quit"],
        )

        self.assertIn(SHELL_OUTPUT_HEADER, output)
        self.assertIn("permission denied", output)

    @patch("restx.core.shell.subprocess.run", side_effect=OSError("boom"))
    def test_repl_inline_shell_fails_gracefully(self, _mock_run):
        output = _run_repl_with_inputs(
            self.spec,
            ["! ls", ".quit"],
        )

        self.assertIn(SHELL_OUTPUT_HEADER, output)
        self.assertIn("Error executing command: boom", output)

    @patch("restx.core.shell.subprocess.call", return_value=0)
    @patch("restx.core.shell._restore_terminal_settings")
    @patch("restx.core.shell._save_terminal_settings", return_value=["saved"])
    def test_repl_shell_drops_to_interactive_shell_and_resumes(
        self,
        _mock_save,
        _mock_restore,
        mock_call,
    ):
        output = _run_repl_with_inputs(
            self.spec,
            [".shell", ".quit"],
        )

        mock_call.assert_called_once()

    @patch("restx.core.shell.subprocess.run")
    def test_bang_alias_executes_inline_shell(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="ok\n",
            stderr="",
            returncode=0,
        )

        output = _run_repl_with_inputs(
            self.spec,
            ["! echo ok", ".quit"],
        )

        self.assertIn("ok", output)
        mock_run.assert_called_once()


class TestV13CurlIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_curl_fixture()

    def test_repl_curl_single_match_flow(self):
        output = _run_repl_with_inputs(
            self.spec,
            [".curl GET /public", ".quit"],
            spec_source="./tests/fixtures/sample_curl.json",
        )

        self.assertIn("curl -X GET", output)
        self.assertNotIn("Select [1-", output)
        self.assertNotIn("├", output)

    def test_repl_curl_multi_match_selection_flow(self):
        output = _run_repl_with_inputs(
            self.spec,
            [".curl /items*", "1", ".quit"],
            spec_source="./tests/fixtures/sample_curl.json",
        )

        self.assertIn("Select [1-2]:", output)
        self.assertIn("1.", output)
        self.assertIn("curl -X", output)

    def test_repl_curl_invalid_selection_continues_repl(self):
        output = _run_repl_with_inputs(
            self.spec,
            [".curl /items*", "99", ".curl GET /public", ".quit"],
            spec_source="./tests/fixtures/sample_curl.json",
        )

        self.assertIn("Invalid selection: 99", output)
        self.assertIn("Choose a number between 1 and 2", output)
        self.assertIn("curl -X GET", output)

    def test_repl_curl_no_match_shows_error(self):
        output = _run_repl_with_inputs(
            self.spec,
            [".curl /missing/*", ".quit"],
            spec_source="./tests/fixtures/sample_curl.json",
        )

        self.assertIn("No endpoints match pattern", output)


class TestV13LsPagerIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    @patch("restx.cli.viewer._read_key", side_effect=["q"])
    @patch("restx.cli.viewer.sys.stdin.isatty", return_value=True)
    @patch("restx.cli.viewer.sys.stdout", new_callable=io.StringIO)
    def test_repl_ls_pager_exits_and_returns_to_repl(
        self,
        _mock_stdout,
        _mock_isatty,
        _mock_read_key,
    ):
        output = _run_repl_with_inputs(
            self.spec,
            [".ls", ".status", ".quit"],
        )

        self.assertIn("Spec title: DSL Test API", output)

    @patch("restx.cli.viewer._read_key", side_effect=[KEY_DOWN, KEY_DOWN, KEY_UP, "q"])
    @patch("restx.cli.viewer.sys.stdin.isatty", return_value=True)
    @patch("restx.cli.viewer.sys.stdout", new_callable=io.StringIO)
    def test_pager_navigation_scroll_and_exit(
        self,
        _mock_stdout,
        _mock_isatty,
        mock_read_key,
    ):
        console = Console(force_terminal=False, no_color=True, width=80)
        run_pager_session(self.spec, console)

        self.assertEqual(mock_read_key.call_count, 4)

    @patch(
        "restx.cli.viewer._read_key",
        side_effect=["/", "G", "E", "T", "\r", "n", "q"],
    )
    @patch("restx.cli.viewer.sys.stdin.isatty", return_value=True)
    @patch("restx.cli.viewer.sys.stdout", new_callable=io.StringIO)
    def test_pager_forward_search_and_next_match(
        self,
        _mock_stdout,
        _mock_isatty,
        mock_read_key,
    ):
        console = Console(force_terminal=False, no_color=True, width=80)
        run_pager_session(self.spec, console)

        self.assertGreaterEqual(mock_read_key.call_count, 6)

    @patch(
        "restx.cli.viewer._read_key",
        side_effect=["?", "d", "o", "g", "\r", "q"],
    )
    @patch("restx.cli.viewer.sys.stdin.isatty", return_value=True)
    @patch("restx.cli.viewer.sys.stdout", new_callable=io.StringIO)
    def test_pager_backward_search_and_exit(
        self,
        _mock_stdout,
        _mock_isatty,
        mock_read_key,
    ):
        console = Console(force_terminal=False, no_color=True, width=80)
        run_pager_session(self.spec, console)

        self.assertGreaterEqual(mock_read_key.call_count, 5)

    @patch("restx.cli.viewer.sys.stdin.isatty", return_value=False)
    def test_pager_non_tty_prints_filtered_buffer(self, _mock_isatty):
        console = Console(force_terminal=False, no_color=True, width=80)
        output = io.StringIO()
        console.file = output

        run_pager_session(self.spec, console, filter_pattern="/users*")

        rendered = output.getvalue()
        self.assertIn("/users", rendered)
        self.assertNotIn("/dogs", rendered)

    @patch("restx.cli.viewer._read_key", side_effect=["q"])
    @patch("restx.cli.viewer.sys.stdin.isatty", return_value=True)
    @patch("restx.cli.viewer.sys.stdout", new_callable=io.StringIO)
    def test_repl_ls_with_pattern_launches_filtered_pager(
        self,
        _mock_stdout,
        _mock_isatty,
        mock_read_key,
    ):
        with patch("restx.cli.repl.run_pager_session") as mock_pager:
            _run_repl_with_inputs(
                self.spec,
                [".ls /users*", ".quit"],
            )

        mock_pager.assert_called_once()
        _, kwargs = mock_pager.call_args
        self.assertEqual(kwargs["filter_pattern"], "/users*")
        mock_read_key.assert_not_called()


class TestV13CommandUnificationIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_colon_prefixed_commands_in_one_session(self):
        output = _run_repl_with_inputs(
            self.spec,
            [
                ".help",
                ".status",
                ".context on",
                "GET /users*",
                ".status",
                ".context off",
                ".context reset",
                ".quit",
            ],
        )

        self.assertIn("RestX Help", output)
        self.assertIn(".ls", output)
        self.assertIn(".curl", output)
        self.assertIn("Contextual query mode enabled", output)
        self.assertIn("Contextual query mode disabled", output)
        self.assertIn("Context mode is not enabled.", output)
        self.assertIn("match", output.lower())

    def test_bare_commands_are_not_meta_commands(self):
        self.assertIsNone(parse_repl_command("ls"))
        self.assertIsNone(parse_repl_command("curl /items"))
        self.assertIsNone(parse_repl_command("shell"))

    def test_bare_ls_runs_as_query_not_pager(self):
        with patch("restx.cli.repl.run_pager_session") as mock_pager:
            output = _run_repl_with_inputs(
                self.spec,
                ["ls", ".quit"],
            )

        mock_pager.assert_not_called()
        self.assertNotIn("API Endpoints", output)

    def test_question_mark_help_alias(self):
        output = _run_repl_with_inputs(
            self.spec,
            [".?", ".quit"],
        )

        self.assertIn("RestX Help", output)

    def test_bare_quit_does_not_exit_repl(self):
        output = _run_repl_with_inputs(
            self.spec,
            ["quit", ".quit"],
        )

        self.assertIn("No matches", output)
        self.assertNotIn("Contextual query mode", output)


class TestV13VisualIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsl_spec = _load_dsl_fixture()
        cls.petstore_spec = _load_petstore_fixture()

    def test_status_line_content_and_truncation(self):
        source = "https://petstore.swagger.io/v2/swagger.json"
        line = format_repl_status_line(source, "Interactive", terminal_width=40)

        self.assertIn("API:", line)
        self.assertIn("| Mode: Interactive", line)
        self.assertIn("...", truncate_source_middle(source, 20))

    def test_status_line_transparent_styling(self):
        line = format_repl_status_line("./spec.json", "Context")
        toolbar = build_repl_status_toolbar(line, terminal_width=80)
        parts = to_formatted_text(toolbar)
        text = "".join(part[1] for part in parts if len(part) > 1)
        styles = [part[0] for part in parts if len(part) > 1]

        self.assertIn("─", text)
        self.assertTrue(styles)
        self.assertTrue(any(STATUS_LINE_FG in style for style in styles))
        self.assertFalse(any("bg:" in style for style in styles if style))

    def test_status_line_ansi_wrapper(self):
        wrapped = wrap_status_line_ansi("API: test | Mode: Browse")
        self.assertIn("| Mode: Browse", wrapped)
        self.assertTrue(wrapped.startswith(STATUS_LINE_ANSI))
        self.assertNotIn("48;", wrapped)

    def test_repl_context_mode_switch_updates_session(self):
        output = _run_repl_with_inputs(
            self.dsl_spec,
            [".context on", ".quit"],
        )

        self.assertIn("Contextual query mode enabled", output)

    def test_endpoint_summary_truncation_at_narrow_width(self):
        endpoint = next(
            ep
            for ep in self.petstore_spec.endpoints
            if ep.path == "/user/createWithList"
        )
        markup = render_endpoint_markup(
            endpoint,
            max_line_width=40,
            tree_prefix_len=4,
        )

        self.assertIn("...", markup)
        self.assertIn("POST", markup)

    def test_search_highlight_uses_lavender_background(self):
        endpoint = next(
            ep
            for ep in self.dsl_spec.endpoints
            if ep.method == "GET" and ep.path == "/users"
        )
        markup = render_endpoint_markup(endpoint, highlighted=True)

        self.assertIn(SEARCH_HIGHLIGHT_STYLE, markup)

    def test_pager_search_highlight_uses_lavender(self):
        highlighted = _format_pager_line("GET /users", highlighted=True)
        self.assertIn(SEARCH_HIGHLIGHT_STYLE, highlighted)
        self.assertIn("#E6E6FA", highlighted)

    def test_pager_selected_line_uses_light_blue_background(self):
        selected = _format_pager_line("GET /users", current=True)
        self.assertIn(PAGER_SELECTED_STYLE, selected)
        self.assertNotIn("[reverse]", selected)

    def test_pager_normal_line_has_no_selection_background(self):
        normal = _format_pager_line("GET /users")
        self.assertNotIn(PAGER_SELECTED_STYLE, normal)
        self.assertNotIn("[reverse]", normal)

    def test_pager_footer_help_text_is_plain(self):
        from restx.core.pager import Pager

        pager = Pager(self.dsl_spec, page_height=10)
        footer = _format_pager_status(
            pager,
            search_input_mode=False,
            search_input_buffer="",
            search_forward=True,
        )
        self.assertIn("API Endpoints", footer)
        self.assertIn("/ search", footer)

    def test_query_results_remain_available_after_meta_commands(self):
        output = _run_repl_with_inputs(
            self.dsl_spec,
            ["GET /users*", ".status", ".quit"],
        )

        self.assertIn("match", output.lower())
        self.assertIn("DSL Test API", output)


class TestV13RegressionIntegration(unittest.TestCase):
    """Ensure v1.3 additions do not break core REPL query workflows."""

    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_slash_path_query_works_as_search(self):
        output = _run_repl_with_inputs(
            self.spec,
            ["/users", ".quit"],
        )

        self.assertIn("match", output.lower())
        self.assertNotIn("Unknown command", output)

    def test_basic_query_flow_still_works(self):
        output = _run_repl_with_inputs(
            self.spec,
            ["GET /users*", ".quit"],
        )

        self.assertIn("2 matches", output)
        self.assertRegex(output, r"GET\s+/users")

    def test_context_mode_query_flow_still_works(self):
        output = _run_repl_with_inputs(
            self.spec,
            [".context on", "GET /users*", "req:email", ".quit"],
        )

        self.assertIn("Contextual query mode enabled", output)
        matches = execute_query("GET /users* req:email", self.spec)
        self.assertTrue(matches)

    def test_context_reset_via_command(self):
        context = QueryContext()
        context.enable()
        context.execute("GET /users*", self.spec)

        output = _run_repl_with_inputs(
            self.spec,
            [".context on", "GET /users*", ".context reset", ".status", ".quit"],
        )

        self.assertIn("Context filter cleared", output)


if __name__ == "__main__":
    unittest.main()
