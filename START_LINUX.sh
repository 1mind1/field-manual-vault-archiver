#!/usr/bin/env bash
# Cross-platform terminal launcher for Linux and macOS (Bash 3.2+).
# The Windows launcher remains separate; the archiver itself is shared.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1

find_python() {
    local candidate
    for candidate in python3 python3.13 python3.12 python3.11 python3.10; do
        if command -v "$candidate" >/dev/null 2>&1 && \
                "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

if ! PYTHON_BIN="$(find_python)"; then
    printf '%s\n' 'Python 3.10 or newer is required.' \
        'Install Python 3 from python.org or your system package manager, then retry.' >&2
    exit 1
fi

# Experienced users can pass native CLI flags, e.g. ./START_LINUX.sh --help.
if (($#)); then
    exec "$PYTHON_BIN" "$SCRIPT_DIR/trex_vault_archiver.py" "$@"
fi

printf '\nFIELD MANUAL VAULT ARCHIVER — LINUX / macOS\n'
printf '%s\n\n' 'Unofficial utility. Existing files are preserved. Discovery is rate-limited.'
DEFAULT_DEST="$HOME/Documents/Field Manual Vault"
read -r -p "Field Manual Vault folder [${DEFAULT_DEST}]: " DEST || exit 0
DEST="${DEST:-$DEFAULT_DEST}"

cat <<'MENU'

CHOOSE AN ACTION
  1  AUDIT EXISTING PDF/EPUB FILES (offline)
  2  BUILD WHOLE-SITE CATALOG ONLY (may take time)
  3  SELECTIVE BULK DOWNLOAD (categories, formats, GB cap)
  4  LEGACY FIVE POPULAR SEARCH TOPICS
  5  COMPLETE ORGANIZED DOWNLOAD (potentially over 100 GB)
  6  REBUILD OFFLINE HTML CATALOG (offline)
  7  INCREMENTAL CATALOG UPDATE (categories, no files downloaded)
MENU

read -r -p "Choose 1-7 [3]: " SELECT || exit 0
SELECT="${SELECT:-3}"
case "$SELECT" in
    1) ARGS=(--mode organized --audit-only) ;;
    2) ARGS=(--mode organized --dry-run) ;;
    3) ARGS=(--mode selective --interactive --estimate-samples 6) ;;
    4) ARGS=(--mode topics) ;;
    5) ARGS=(--mode organized) ;;
    6) ARGS=(--index-only) ;;
    7) ARGS=(--mode selective --interactive --update-only) ;;
    *) echo 'Invalid choice.' >&2; exit 2 ;;
esac

if [[ "$SELECT" != 1 && "$SELECT" != 6 ]]; then
    cat <<'SPEED_MENU'

DISCOVERY REQUEST PACING (shared limit across workers)
  1  Gentle:   1 worker, >= 1.25 seconds between HTML requests
  2  Balanced: 2 workers, >= 0.55 seconds (default)
  3  Faster:   3 workers, >= 0.50 seconds
SPEED_MENU
    read -r -p "Choose 1-3 [2]: " SPEED || exit 0
    SPEED="${SPEED:-2}"
    case "$SPEED" in
        1) ARGS+=(--workers 1 --request-interval 1.25) ;;
        2) ARGS+=(--workers 2 --request-interval 0.55) ;;
        3) ARGS+=(--workers 3 --request-interval 0.5) ;;
        *) echo 'Invalid speed choice.' >&2; exit 2 ;;
    esac
fi

if [[ "$SELECT" == 5 ]]; then
    echo 'CAUTION: Can consume over 100 GB. Complete site discovery is not guaranteed.'
    read -r -p 'Type YES to attempt a full archive: ' CONFIRM || exit 0
    [[ "$CONFIRM" == YES ]] || { echo 'Cancelled. No download started.'; exit 0; }
elif [[ "$SELECT" == 4 ]]; then
    echo 'CAUTION: The legacy topic downloader has no per-run storage limit by default.'
    read -r -p 'Type YES to download legacy topics: ' CONFIRM || exit 0
    [[ "$CONFIRM" == YES ]] || { echo 'Cancelled. No download started.'; exit 0; }
fi

"$PYTHON_BIN" "$SCRIPT_DIR/trex_vault_archiver.py" --dest "$DEST" "${ARGS[@]}"
status=$?
if ((status == 0)); then
    echo 'Completed.'
else
    echo "Exit status: $status. Check messages above." >&2
fi
# On macOS a double-clicked .command opens Terminal. Keep that window readable.
if [[ "${TREX_LAUNCHER_PAUSE:-0}" == 1 && -t 0 ]]; then
    read -r -p 'Press Return to close this window...' _ || true
fi
exit "$status"
