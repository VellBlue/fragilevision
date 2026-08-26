#!/bin/bash
# FragileVision.app launcher — Contents/MacOS/FragileVision
#
# Runs with no controlling terminal (Finder and the Dock launch it directly),
# so failures that would normally print to a shell go to a log file and, for
# anything that stops the app from starting at all, a native dialog instead.
set -u

PORT=7331
LOG_DIR="$HOME/Library/Logs/FragileVision"
LOG_FILE="$LOG_DIR/fragilevision.log"
mkdir -p "$LOG_DIR"

fail() {
    osascript -e "display alert \"FragileVision non può avviarsi\" message \"$1\" as critical" >/dev/null 2>&1
    printf '%s ERRORE: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG_FILE"
    exit 1
}

# A double-click from Finder does not inherit the interactive shell's PATH,
# so the common install locations are tried explicitly before falling back
# to whatever `command -v` finds.
PYTHON=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 "$(command -v python3 2>/dev/null)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ] \
        && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
[ -n "$PYTHON" ] || fail "Serve Python 3.11 o più recente. Installalo (python.org, o \"brew install python\") e riapri l'app."

# Already running: bring the browser to the existing instance instead of a
# second server that would fail to bind the same port anyway.
if curl -sf -o /dev/null -m 1 "http://127.0.0.1:$PORT/api/bootstrap" 2>/dev/null; then
    open "http://127.0.0.1:$PORT"
    exit 0
fi

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$BUNDLE_DIR/Resources/app"
[ -d "$APP_DIR/fragilevision" ] || fail "Il pacchetto dell'app è incompleto: manca $APP_DIR/fragilevision. Ricostruiscilo con scripts/build_macos_app.sh."

cd "$APP_DIR" || fail "Impossibile raggiungere $APP_DIR."
# -u: stdout is block-buffered against a file instead of a terminal, so
# without it the log can stay empty for a long time, or lose everything if
# the app is force-quit rather than exiting cleanly.
exec "$PYTHON" -u -m fragilevision --port "$PORT" >> "$LOG_FILE" 2>&1
