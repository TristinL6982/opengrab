#!/usr/bin/env python3
"""OpenGrab entry point.

Usage:
  python -m opengrab --help
  python -m opengrab --version
  python -m opengrab --list-plugins
  python -m opengrab --smoke-test
  python -m opengrab [--port N] [--bind H] [--headless]
"""

import sys
from pathlib import Path

# Ensure we can import from the local source tree
sys.path.insert(0, str(Path(__file__).parent))

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())