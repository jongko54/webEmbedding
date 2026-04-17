#!/usr/bin/env python3
"""MCP server entrypoint for the source-first clone workflow."""

from __future__ import annotations

import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from source_first_clone.protocol import serve


if __name__ == "__main__":
    raise SystemExit(serve())
