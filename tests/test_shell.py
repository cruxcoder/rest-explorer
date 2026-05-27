"""Unit tests for shell integration."""

import unittest
from unittest.mock import MagicMock, patch

from restx.core.shell import launch_interactive_shell, run_shell_command


class TestRunShellCommand(unittest.TestCase):
    @patch("restx.core.shell.subprocess.run")
    def test_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="hello\n",
            stderr="",
            returncode=0,
        )
        self.assertEqual(run_shell_command("echo hello"), "hello")

    @patch("restx.core.shell.subprocess.run")
    def test_combines_stdout_and_stderr(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="out\n",
            stderr="err\n",
            returncode=1,
        )
        self.assertEqual(run_shell_command("bad"), "out\nerr")

    @patch("restx.core.shell.subprocess.run")
    def test_nonzero_exit_without_output(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="",
            returncode=127,
        )
        self.assertEqual(run_shell_command("missing"), "Command exited with code 127")

    @patch("restx.core.shell.subprocess.run", side_effect=OSError("boom"))
    def test_os_error_is_reported(self, _mock_run):
        self.assertEqual(run_shell_command("ls"), "Error executing command: boom")

    def test_empty_command_returns_empty_string(self):
        self.assertEqual(run_shell_command(""), "")
        self.assertEqual(run_shell_command("   "), "")

    @patch("restx.core.shell.subprocess.run")
    def test_uses_shell_true(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        run_shell_command("ls -ltr")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs["shell"])
        self.assertTrue(kwargs["capture_output"])


class TestLaunchInteractiveShell(unittest.TestCase):
    @patch("restx.core.shell.subprocess.call", return_value=0)
    @patch("restx.core.shell._restore_terminal_settings")
    @patch("restx.core.shell._save_terminal_settings", return_value=["saved"])
    @patch("restx.core.shell.os.environ.get", return_value="/bin/bash")
    def test_launches_default_shell(
        self,
        mock_env_get,
        mock_save,
        mock_restore,
        mock_call,
    ):
        launch_interactive_shell()
        mock_env_get.assert_called_once_with("SHELL", "/bin/bash")
        mock_save.assert_called_once()
        mock_call.assert_called_once_with(["/bin/bash"])
        mock_restore.assert_called_once_with(["saved"])

    @patch("restx.core.shell.subprocess.call", return_value=0)
    @patch("restx.core.shell._restore_terminal_settings")
    @patch("restx.core.shell._save_terminal_settings", return_value=None)
    def test_restores_even_when_no_tty_settings(self, mock_save, mock_restore, _mock_call):
        launch_interactive_shell()
        mock_save.assert_called_once()
        mock_restore.assert_called_once_with(None)

    @patch("restx.core.shell.sys.stderr")
    @patch("restx.core.shell.subprocess.call", return_value=2)
    @patch("restx.core.shell._restore_terminal_settings")
    @patch("restx.core.shell._save_terminal_settings", return_value=["saved"])
    def test_reports_nonzero_shell_exit(self, _mock_save, mock_restore, _mock_call, mock_stderr):
        launch_interactive_shell()
        mock_restore.assert_called_once_with(["saved"])
        mock_stderr.write.assert_called_once()
        self.assertIn("Shell exited with code 2", mock_stderr.write.call_args[0][0])

    @patch("restx.core.shell.sys.stderr")
    @patch("restx.core.shell.subprocess.call", side_effect=OSError("cannot exec"))
    @patch("restx.core.shell._restore_terminal_settings")
    @patch("restx.core.shell._save_terminal_settings", return_value=["saved"])
    def test_reports_launch_failure(self, _mock_save, mock_restore, _mock_call, mock_stderr):
        launch_interactive_shell()
        mock_restore.assert_called_once_with(["saved"])
        mock_stderr.write.assert_called_once()
        self.assertIn("Failed to launch shell", mock_stderr.write.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
