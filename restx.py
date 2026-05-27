#!/usr/bin/env python3
"""
RestX CLI Entry Point

Usage:
    python restx.py [SPEC_URL_OR_FILE] [OPTIONS]
    cat spec.json | python restx.py

Run 'python restx.py --help' for more information.
"""

import sys
import os

# Ensure the package root is in the path if running as script
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from restx.cli.main import main

if __name__ == "__main__":
    main()
