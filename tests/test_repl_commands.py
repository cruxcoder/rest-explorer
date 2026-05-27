"""Unit tests for REPL meta-commands (/shell, /!, /curl, /ls)."""

import io
import os
import unittest
from unittest.mock import MagicMock, patch

from rich.console import Console

from restx.cli.commands import (
    SHELL_OUTPUT_HEADER,
    build_repl_help_lines,
    command_should_exit,
    display_repl_help,
    format_curl_output,
    handle_curl_command,
    handle_curl_selection,
    handle_shell_inline,
    parse_repl_command,
)
from restx.cli.repl import _handle_repl_command
from restx.cli.tree_view import KEY_DOWN
from restx.cli.viewer import run_pager_session
from restx.core import QueryContext, load_spec_from_file

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _console_print_text(console) -> str:
    """Collect text passed to a mocked Rich console.print()."""
    parts: list[str] = []
    for call in console.print.call_args_list:
        if call.args:
            parts.append(str(call.args[0]))
        elif call.kwargs:
            parts.append(str(call.kwargs.get("render", "")))
    return "\n".join(parts)


def _load_curl_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_curl.json")
    return load_spec_from_file(path)


def _load_dsl_fixture():
    path = os.path.join(FIXTURES_DIR, "sample_dsl.json")
    return load_spec_from_file(path)


class TestReplCommandParsing(unittest.TestCase):
    def test_parse_dot_prefixed_commands(self):
        self.assertEqual(parse_repl_command(".shell").name, "shell")
        self.assertEqual(parse_repl_command(".ls").name, "ls")
        self.assertEqual(parse_repl_command(".ls /user/*").args, "/user/*")
        self.assertEqual(parse_repl_command(".curl /items").args, "/items")
        self.assertIsNone(parse_repl_command(".! ls -ltr"))
        self.assertEqual(parse_repl_command("!ls").name, "shell_inline")
        self.assertEqual(parse_repl_command("!ls").args, "ls")
        self.assertEqual(parse_repl_command("! ls -ltr").name, "shell_inline")
        self.assertEqual(parse_repl_command("! ls -ltr").args, "ls -ltr")
        self.assertEqual(parse_repl_command(".help").name, "help")
        self.assertEqual(parse_repl_command(".?").name, "help")
        self.assertEqual(parse_repl_command(".status").name, "status")
        self.assertEqual(parse_repl_command(".clear").name, "clear")
        self.assertEqual(parse_repl_command(".quit").name, "quit")
        self.assertEqual(parse_repl_command(".q").name, "quit")
        self.assertEqual(parse_repl_command(".context on").name, "context_on")
        self.assertEqual(parse_repl_command(".context off").name, "context_off")
        self.assertEqual(parse_repl_command(".context reset").name, "context_reset")
        self.assertEqual(parse_repl_command(".load ./spec.json").name, "load")
        self.assertEqual(parse_repl_command(".load ./spec.json").args, "./spec.json")

    def test_dot_prefix_regex_patterns_are_search_queries(self):
        self.assertIsNone(parse_repl_command(".+"))
        self.assertIsNone(parse_repl_command("./users"))
        self.assertIsNone(parse_repl_command(".reset"))

    def test_path_query_starting_with_slash_is_not_meta_command(self):
        self.assertIsNone(parse_repl_command("/users*"))
        self.assertIsNone(parse_repl_command("/users"))

    def test_leading_space_prevents_dot_command(self):
        self.assertIsNone(parse_repl_command(" .ls"))

    def test_slash_prefixed_commands_are_not_meta_commands(self):
        self.assertIsNone(parse_repl_command("/help"))
        self.assertIsNone(parse_repl_command("/ls"))
        self.assertIsNone(parse_repl_command("/q"))

    def test_bare_commands_are_not_meta_commands(self):
        self.assertIsNone(parse_repl_command("help"))
        self.assertIsNone(parse_repl_command("context on"))
        self.assertIsNone(parse_repl_command("context off"))
        self.assertIsNone(parse_repl_command("reset"))
        self.assertIsNone(parse_repl_command("status"))
        self.assertIsNone(parse_repl_command("quit"))
        self.assertFalse(command_should_exit("quit"))
        self.assertFalse(command_should_exit("exit"))
        self.assertFalse(command_should_exit("q"))
        self.assertTrue(command_should_exit(".quit"))
        self.assertTrue(command_should_exit(".q"))
        self.assertFalse(command_should_exit("GET /users*"))
        self.assertIsNone(parse_repl_command("ls"))
        self.assertIsNone(parse_repl_command("curl /items"))
        self.assertIsNone(parse_repl_command("shell"))

    def test_non_command_returns_none(self):
        self.assertIsNone(parse_repl_command("GET /users*"))


class TestReplHelpCommand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_help_lines_list_all_meta_commands(self):
        lines = build_repl_help_lines(self.spec)
        text = "\n".join(lines)
        self.assertIn(".help", text)
        self.assertIn(".?", text)
        self.assertIn(".clear", text)
        self.assertIn(".ls", text)
        self.assertIn(".curl", text)
        self.assertIn(".shell", text)
        self.assertIn("! <cmd>", text)
        self.assertIn(".load", text)
        self.assertNotIn(".!", text)
        self.assertIn(".context reset", text)
        self.assertNotIn("Mode A", text)
        self.assertNotIn("Mode B", text)
        self.assertNotIn("Legacy aliases", text)
        self.assertIn("Query DSL:", text)

    def test_handle_repl_command_help_returns_immediately(self):
        console = MagicMock()
        context = QueryContext()
        handled, should_exit, pending, show_hint, reload = _handle_repl_command(
            ".help",
            self.spec,
            context,
            console,
            True,
        )
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertIsNone(pending)
        self.assertTrue(show_hint)
        self.assertIsNone(reload)
        output = _console_print_text(console)
        self.assertIn("RestX Help", output)

    def test_handle_repl_command_question_mark_alias(self):
        console = MagicMock()
        context = QueryContext()
        handled, should_exit, pending, show_hint, reload = _handle_repl_command(
            ".?",
            self.spec,
            context,
            console,
            True,
        )
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertTrue(show_hint)
        self.assertIsNone(reload)
        output = _console_print_text(console)
        self.assertIn("RestX Help", output)

    def test_bare_help_is_not_a_meta_command(self):
        console = MagicMock()
        context = QueryContext()
        handled, _, _, show_hint, reload = _handle_repl_command(
            "help",
            self.spec,
            context,
            console,
            True,
        )
        self.assertFalse(handled)
        self.assertFalse(show_hint)
        self.assertIsNone(reload)

    def test_display_repl_help_prints_help_block(self):
        console = MagicMock()
        display_repl_help(console, self.spec)
        self.assertTrue(console.print.called)
        output = _console_print_text(console)
        self.assertIn("RestX Help", output)

    @patch("restx.cli.repl.clear_repl_screen")
    def test_handle_repl_command_clear(self, mock_clear):
        console = MagicMock()
        context = QueryContext()
        handled, should_exit, pending, show_hint, reload = _handle_repl_command(
            ".clear",
            self.spec,
            context,
            console,
            True,
        )
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertIsNone(pending)
        self.assertFalse(show_hint)
        mock_clear.assert_called_once_with(console)


class TestContextResetCommand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    def test_context_reset_when_disabled(self):
        console = MagicMock()
        context = QueryContext()
        handled, should_exit, pending, show_hint, reload = _handle_repl_command(
            ".context reset",
            self.spec,
            context,
            console,
            True,
        )
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertIsNone(pending)
        self.assertFalse(show_hint)
        output = _console_print_text(console)
        self.assertIn("Context mode is not enabled.", output)

    def test_context_reset_when_enabled(self):
        console = MagicMock()
        context = QueryContext()
        context.enable()
        context.execute("GET /users*", self.spec)
        handled, should_exit, pending, show_hint, reload = _handle_repl_command(
            ".context reset",
            self.spec,
            context,
            console,
            True,
        )
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertFalse(context.filter_parts)
        output = _console_print_text(console)
        self.assertIn("Context filter cleared.", output)

    def test_reset_dot_prefix_is_not_handled_as_command(self):
        console = MagicMock()
        context = QueryContext()
        handled, should_exit, pending, show_hint, reload = _handle_repl_command(
            ".reset",
            self.spec,
            context,
            console,
            True,
        )
        self.assertFalse(handled)
        self.assertFalse(should_exit)


class TestShellInlineCommand(unittest.TestCase):
    @patch("restx.cli.commands.run_shell_command", return_value="file.txt")
    def test_shell_inline_prints_header_and_output(self, _mock_run):
        console = MagicMock()
        handle_shell_inline(console, "ls -ltr")

        output = _console_print_text(console)
        self.assertIn(SHELL_OUTPUT_HEADER, output)
        self.assertIn("file.txt", output)

    @patch("restx.cli.commands.run_shell_command", return_value="permission denied")
    def test_shell_inline_shows_stderr_output(self, _mock_run):
        console = MagicMock()
        handle_shell_inline(console, "bad-command")

        output = _console_print_text(console)
        self.assertIn("permission denied", output)

    def test_shell_inline_empty_command_shows_usage(self):
        console = MagicMock()
        handle_shell_inline(console, " ")
        output = _console_print_text(console)
        self.assertIn("Usage: ! <shell_command>", output)
        self.assertNotIn(SHELL_OUTPUT_HEADER, output)


class TestCurlCommand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_curl_fixture()

    def test_single_match_returns_curl(self):
        result = handle_curl_command(self.spec, "GET /public")
        self.assertEqual(result.kind, "curl")
        self.assertIn("curl -X GET", result.message)
        self.assertNotIn("├", result.message)

    def test_multi_match_returns_selection_prompt(self):
        result = handle_curl_command(self.spec, "/items*")
        self.assertEqual(result.kind, "select")
        self.assertIn("1.", result.message)
        self.assertIn("Select [1-2]:", result.message)
        self.assertEqual(len(result.endpoints), 2)

    def test_no_match_returns_error(self):
        result = handle_curl_command(self.spec, "/missing/*")
        self.assertEqual(result.kind, "error")
        self.assertIn("No endpoints match", result.message)

    def test_selection_generates_curl(self):
        initial = handle_curl_command(self.spec, "/items*")
        endpoint = initial.endpoints[0]
        selected = handle_curl_selection(self.spec, initial.endpoints, 1)
        self.assertEqual(selected.kind, "curl")
        self.assertIn(f"curl -X {endpoint.method}", selected.message)
        self.assertIn(endpoint.path, selected.message)

    def test_invalid_selection_returns_error(self):
        initial = handle_curl_command(self.spec, "/items*")
        selected = handle_curl_selection(self.spec, initial.endpoints, 99)
        self.assertEqual(selected.kind, "error")

    def test_curl_output_is_plain_text_block(self):
        result = handle_curl_command(self.spec, "POST /items")
        formatted = format_curl_output(result.message)
        self.assertTrue(formatted.startswith("curl -X POST"))
        self.assertIn("<string>", formatted)


class TestReplCurlHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_curl_fixture()

    def test_handle_repl_command_curl_single_match(self):
        console = MagicMock()
        context = QueryContext()
        handled, should_exit, pending, show_hint, reload = _handle_repl_command(
            ".curl GET /public",
            self.spec,
            context,
            console,
            True,
        )
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertIsNone(pending)
        self.assertFalse(show_hint)
        output = _console_print_text(console)
        self.assertIn("curl -X GET", output)

    def test_handle_repl_command_curl_multi_match(self):
        console = MagicMock()
        context = QueryContext()
        handled, should_exit, pending, show_hint, reload = _handle_repl_command(
            ".curl /items*",
            self.spec,
            context,
            console,
            True,
        )
        self.assertTrue(handled)
        self.assertIsNotNone(pending)
        self.assertEqual(len(pending), 2)
        self.assertFalse(show_hint)


class TestShellInteractiveHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    @patch("restx.cli.repl.handle_shell_interactive")
    def test_handle_repl_command_shell(self, mock_shell):
        console = MagicMock()
        context = QueryContext()
        handled, should_exit, pending, show_hint, reload = _handle_repl_command(
            ".shell",
            self.spec,
            context,
            console,
            True,
        )
        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertFalse(show_hint)
        mock_shell.assert_called_once()


class TestLoadCommand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()
        cls.fixture_path = os.path.join(FIXTURES_DIR, "sample_dsl.json")

    @patch("restx.cli.repl.pad_prompt_to_bottom")
    @patch("restx.cli.repl.clear_repl_screen")
    @patch("restx.cli.repl.load_spec_with_spinner")
    def test_handle_repl_command_load(
        self,
        mock_load,
        mock_clear,
        mock_pad,
    ):
        mock_load.return_value = self.spec
        console = MagicMock()
        context = QueryContext()
        context.enable()
        context.filter_parts.append("GET /users*")
        context.match_results.set_matches(self.spec.endpoints[:1])

        handled, should_exit, pending, show_hint, reload = _handle_repl_command(
            f".load {self.fixture_path}",
            self.spec,
            context,
            console,
            True,
        )

        self.assertTrue(handled)
        self.assertFalse(should_exit)
        self.assertIsNone(pending)
        self.assertTrue(show_hint)
        self.assertEqual(reload, (self.spec, self.fixture_path))
        mock_load.assert_called_once_with(self.fixture_path)
        mock_clear.assert_called_once_with(console)
        mock_pad.assert_called_once()
        self.assertFalse(context.filter_parts)
        self.assertFalse(context.match_results.has_matches)
        output = _console_print_text(console)
        self.assertIn("Loaded spec:", output)

    @patch("restx.cli.repl.load_spec_with_spinner")
    def test_handle_repl_command_load_missing_arg(self, _mock_load):
        console = MagicMock()
        context = QueryContext()
        handled, _, _, show_hint, reload = _handle_repl_command(
            ".load",
            self.spec,
            context,
            console,
            True,
        )
        self.assertTrue(handled)
        self.assertFalse(show_hint)
        self.assertIsNone(reload)
        output = _console_print_text(console)
        self.assertIn("Usage: .load <file_or_url>", output)


class TestLsPagerHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = _load_dsl_fixture()

    @patch("restx.cli.repl.run_pager_session")
    def test_handle_repl_command_ls(self, mock_pager):
        console = MagicMock()
        context = QueryContext()
        handled, should_exit, pending, show_hint, reload = _handle_repl_command(
            ".ls",
            self.spec,
            context,
            console,
            True,
        )
        self.assertTrue(handled)
        self.assertTrue(show_hint)
        self.assertIsNone(reload)
        mock_pager.assert_called_once_with(
            self.spec,
            console,
            filter_pattern=None,
        )

    @patch("restx.cli.repl.run_pager_session")
    def test_handle_repl_command_ls_with_pattern(self, mock_pager):
        console = MagicMock()
        context = QueryContext()
        handled, _, _, show_hint, reload = _handle_repl_command(
            ".ls /users*",
            self.spec,
            context,
            console,
            True,
        )
        self.assertTrue(handled)
        self.assertTrue(show_hint)
        self.assertIsNone(reload)
        mock_pager.assert_called_once_with(
            self.spec,
            console,
            filter_pattern="/users*",
        )

    @patch("restx.cli.viewer._read_key", side_effect=["q"])
    @patch("restx.cli.viewer.sys.stdin.isatty", return_value=True)
    @patch("restx.cli.viewer.sys.stdout", new_callable=io.StringIO)
    def test_pager_session_exits_on_q(self, _mock_stdout, _mock_isatty, _mock_read_key):
        console = Console(force_terminal=False, no_color=True, width=80)
        run_pager_session(self.spec, console)

    @patch("restx.cli.viewer._pager_page_height", return_value=5)
    @patch("restx.cli.viewer._read_key", side_effect=[KEY_DOWN, "q"])
    @patch("restx.cli.viewer.sys.stdin.isatty", return_value=True)
    @patch("restx.cli.viewer.sys.stdout", new_callable=io.StringIO)
    def test_pager_session_scrolls_without_reclearing(
        self,
        mock_stdout,
        _mock_isatty,
        _mock_read_key,
        _mock_page_height,
    ):
        console = Console(force_terminal=False, no_color=True, width=80)
        run_pager_session(self.spec, console)
        self.assertIn("\x1b[S", mock_stdout.getvalue())

    @patch("restx.cli.viewer.sys.stdin.isatty", return_value=False)
    def test_pager_non_tty_prints_buffer(self, _mock_isatty):
        console = MagicMock()
        run_pager_session(self.spec, console, filter_pattern="/users*")
        output = _console_print_text(console)
        self.assertIn("/users", output)


if __name__ == "__main__":
    unittest.main()
