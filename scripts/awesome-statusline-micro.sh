#!/usr/bin/env bash
# ============================================================================
# Antigravity CLI Awesome Statusline - MICRO (1-Line)
# ============================================================================
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/statusline_engine.py" --size micro "$@"
