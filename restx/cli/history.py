"""Persistent REPL command history."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from prompt_toolkit.history import History

DEFAULT_HISTORY_PATH = Path.home() / ".restx_history"
DEFAULT_HISTORY_MAX = 10_000


def history_max_entries() -> int:
    """Return configured max history entries from RESTX_HISTORY_MAX or default."""
    raw = os.environ.get("RESTX_HISTORY_MAX")
    if raw is None:
        return DEFAULT_HISTORY_MAX
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_HISTORY_MAX
    return max(value, 1)


class BoundedFileHistory(History):
    """Plain-text file history with one query per line and a max entry limit."""

    def __init__(
        self,
        filename: str | os.PathLike[str],
        max_entries: int = DEFAULT_HISTORY_MAX,
    ) -> None:
        self.filename = str(filename)
        self.max_entries = max_entries
        super().__init__()
        self._trim_file()

    def load_history_strings(self) -> Iterable[str]:
        entries = self._read_entries()
        return reversed(entries)

    def store_string(self, string: str) -> None:
        entries = self._read_entries()
        entries.append(string)
        self._write_entries(entries[-self.max_entries :])

    def _read_entries(self) -> list[str]:
        path = Path(self.filename)
        if not path.exists():
            return []

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

        return [line for line in lines if line]

    def _write_entries(self, entries: list[str]) -> None:
        path = Path(self.filename)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "\n".join(entries) + ("\n" if entries else ""),
                encoding="utf-8",
            )
        except OSError:
            return

    def _trim_file(self) -> None:
        entries = self._read_entries()
        if len(entries) <= self.max_entries:
            return
        self._write_entries(entries[-self.max_entries :])


def create_history(
    path: Path | str | None = None,
    max_entries: int | None = None,
) -> BoundedFileHistory:
    """Create a bounded file history for the REPL."""
    history_path = Path(path).expanduser() if path is not None else DEFAULT_HISTORY_PATH
    limit = max_entries if max_entries is not None else history_max_entries()
    return BoundedFileHistory(history_path, max_entries=limit)
