#!/usr/bin/env bash
# Double-click from Finder, or run: bash START_MACOS.command
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
export TREX_LAUNCHER_PAUSE=1
exec /bin/bash "$SCRIPT_DIR/START_LINUX.sh" "$@"
