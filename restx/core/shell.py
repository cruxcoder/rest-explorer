"""Shell integration for inline and interactive command execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

try:
    import termios
except ImportError:  # pragma: no cover - Windows
    termios = None  # type: ignore[assignment]


def run_shell_command(cmd: str) -> str:
    """Execute a shell command and return combined stdout/stderr output."""
    stripped = cmd.strip()
    if not stripped:
        return ""

    try:
        result = subprocess.run(
            stripped,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except OSError as exc:
        return f"Error executing command: {exc}"

    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout.rstrip("\n"))
    if result.stderr:
        parts.append(result.stderr.rstrip("\n"))
    if result.returncode != 0 and not parts:
        parts.append(f"Command exited with code {result.returncode}")
    return "\n".join(parts)


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _save_terminal_settings() -> list | None:
    if termios is None or not _stdin_is_tty():
        return None
    try:
        return termios.tcgetattr(sys.stdin.fileno())
    except termios.error:
        return None


def _restore_terminal_settings(settings: list | None) -> None:
    if termios is None or settings is None or not _stdin_is_tty():
        return
    try:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, settings)
    except termios.error:
        pass


def launch_interactive_shell() -> None:
    """Suspend the app, spawn an interactive shell, and restore terminal state."""
    shell = os.environ.get("SHELL", "/bin/bash")
    if not shutil.which(shell):
        sys.stderr.write(f"Failed to launch shell: {shell!r} not found\n")
        return

    saved_settings = _save_terminal_settings()
    try:
        return_code = subprocess.call([shell])
        if return_code not in (0, 130):  # 130 = interrupted by Ctrl+C
            sys.stderr.write(
                f"Shell exited with code {return_code}. "
                "Run /context reset if the terminal state looks wrong.\n"
            )
    except OSError as exc:
        sys.stderr.write(f"Failed to launch shell: {exc}\n")
    finally:
        _restore_terminal_settings(saved_settings)
