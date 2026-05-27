"""CLI Entry Point Logic - Handles argument parsing and dispatching."""

import argparse
import sys
from restx.cli.commands import handle_help, handle_version, handle_run

def create_parser():
    parser = argparse.ArgumentParser(
        prog="restx",
        description="Interactive command-line tool for exploring REST API specifications.",
        add_help=False
    )
    parser.add_argument("spec_source", nargs="?", default=None,
                        help="URL, file path, or stdin (omit for stdin)")
    parser.add_argument("--color", choices=["always", "never", "auto"],
                        default="auto", help="Enable/disable colorization (default: auto)")
    parser.add_argument("--version", action="store_true",
                        help="Show program version number")
    parser.add_argument("--help", "-h", action="store_true",
                        help="Show this help message and exit")
    return parser

def main():
    parser = create_parser()
    args = parser.parse_args()
    if args.help:
        handle_help(args)
        sys.exit(0)
    if args.version:
        handle_version(args)
        sys.exit(0)
    handle_run(args)

if __name__ == "__main__":
    main()
