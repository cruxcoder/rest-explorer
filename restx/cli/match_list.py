"""Aligned match list rendering for the RestX REPL."""

from __future__ import annotations

from restx.core.matcher import (
    format_match_line,
    format_params,
    match_header_segment,
    match_header_width,
    match_method_width,
)
from restx.core.models import Endpoint


def match_list_header_width(matches: list[Endpoint]) -> int:
    """Calculate the maximum width of the ``[N] METHOD PATH`` segment."""
    return match_header_width(matches)


def _wrap_path_segments(
    path: str,
    first_width: int,
    continuation_width: int,
) -> list[str]:
    """Split a path into segments that fit the available line widths."""
    if first_width <= 0:
        first_width = 1
    if continuation_width <= 0:
        continuation_width = 1
    if len(path) <= first_width:
        return [path]

    segments: list[str] = []
    remaining = path
    current_width = first_width

    while remaining:
        if len(remaining) <= current_width:
            segments.append(remaining)
            break

        split_at = remaining.rfind("/", 1, current_width + 1)
        if split_at <= 0:
            split_at = current_width

        segments.append(remaining[:split_at])
        remaining = remaining[split_at:]
        if remaining.startswith("/") and split_at > 0:
            remaining = remaining[1:]
        current_width = continuation_width

    return segments


def format_match_list_lines(
    matches: list[Endpoint],
    *,
    terminal_width: int | None = None,
    indent: str = "  ",
) -> list[str]:
    """Format aligned match lines, optionally wrapping long paths."""
    if not matches:
        return []

    method_width = match_method_width(matches)
    header_width = match_list_header_width(matches)
    lines: list[str] = []

    for index, endpoint in enumerate(matches, start=1):
        params = format_params(endpoint)
        params_suffix = f" params: {params}" if params else ""
        prefix = f"[{index}] {endpoint.method:<{method_width}} "

        if terminal_width is None or terminal_width <= 0:
            lines.append(
                format_match_line(
                    index,
                    endpoint,
                    header_width=header_width,
                    method_width=method_width,
                    indent=indent,
                )
            )
            continue

        prefix_len = len(prefix)
        first_path_width = max(terminal_width - len(indent) - prefix_len, 1)
        continuation_path_width = max(
            terminal_width - len(indent) - prefix_len,
            1,
        )
        path_segments = _wrap_path_segments(
            endpoint.path,
            first_path_width,
            continuation_path_width,
        )

        for segment_index, path_segment in enumerate(path_segments):
            if segment_index == 0:
                content = prefix + path_segment
            else:
                content = " " * prefix_len + path_segment

            if segment_index == len(path_segments) - 1:
                content = content.ljust(header_width) + params_suffix

            lines.append(f"{indent}{content}")

    return lines


def params_column_index(matches: list[Endpoint], *, indent: str = "  ") -> int:
    """Return the zero-based column where ``params:`` starts for aligned lines."""
    return len(indent) + match_list_header_width(matches) + 1


def _append_match_params(output, params: str) -> None:
    """Append styled parameter columns to a Rich ``Text`` object."""
    from restx.cli.colors import BASE_TEXT_STYLE, PARAM_NAME_STYLE

    output.append(" params: ", style=BASE_TEXT_STYLE)
    for part_index, part in enumerate(_split_param_parts(params)):
        if part_index:
            output.append(", ", style=BASE_TEXT_STYLE)
        name, suffix = _split_param_name(part)
        output.append(name, style=PARAM_NAME_STYLE)
        if suffix:
            output.append(suffix, style=BASE_TEXT_STYLE)


def _split_param_parts(params: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in params:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current).strip())
    return parts


def _split_param_name(part: str) -> tuple[str, str]:
    marker = " (path)"
    if part.endswith(marker):
        return part[: -len(marker)], marker
    return part, ""


def format_match_list_rich_text(
    matches: list[Endpoint],
    *,
    terminal_width: int | None = None,
    indent: str = "  ",
):
    """Format aligned match lines with light-theme colors."""
    from rich.text import Text

    from restx.cli.colors import BASE_TEXT_STYLE, METHOD_STYLES, PATH_STYLE

    if not matches:
        return Text()

    method_width = match_method_width(matches)
    header_width = match_list_header_width(matches)
    output = Text()

    for index, endpoint in enumerate(matches, start=1):
        params = format_params(endpoint)
        prefix = f"[{index}] "
        method_gap = " " * (method_width - len(endpoint.method) + 1)

        if terminal_width is None or terminal_width <= 0:
            header = match_header_segment(
                index,
                endpoint,
                method_width=method_width,
            )
            output.append(indent)
            output.append("[")
            output.append(str(index), style=BASE_TEXT_STYLE)
            output.append("] ", style=BASE_TEXT_STYLE)
            output.append(
                endpoint.method,
                style=METHOD_STYLES.get(endpoint.method, BASE_TEXT_STYLE),
            )
            output.append(method_gap, style=BASE_TEXT_STYLE)
            output.append(endpoint.path, style=PATH_STYLE)
            padding = header_width - len(header)
            if padding > 0:
                output.append(" " * padding, style=BASE_TEXT_STYLE)
            if params:
                _append_match_params(output, params)
            output.append("\n")
            continue

        prefix_len = len(prefix) + method_width + 1
        first_path_width = max(terminal_width - len(indent) - prefix_len, 1)
        continuation_path_width = max(
            terminal_width - len(indent) - prefix_len,
            1,
        )
        path_segments = _wrap_path_segments(
            endpoint.path,
            first_path_width,
            continuation_path_width,
        )

        for segment_index, path_segment in enumerate(path_segments):
            if segment_index == 0:
                output.append(indent)
                output.append("[")
                output.append(str(index), style=BASE_TEXT_STYLE)
                output.append("] ", style=BASE_TEXT_STYLE)
                output.append(
                    endpoint.method,
                    style=METHOD_STYLES.get(endpoint.method, BASE_TEXT_STYLE),
                )
                output.append(method_gap, style=BASE_TEXT_STYLE)
                output.append(path_segment, style=PATH_STYLE)
            else:
                output.append(indent)
                output.append(" " * prefix_len, style=BASE_TEXT_STYLE)
                output.append(path_segment, style=PATH_STYLE)

            if segment_index == len(path_segments) - 1:
                content_len = prefix_len + len(path_segment)
                padding = header_width - content_len
                if padding > 0:
                    output.append(" " * padding, style=BASE_TEXT_STYLE)
                if params:
                    _append_match_params(output, params)

            output.append("\n")

    return output
