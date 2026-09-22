#!/bin/bash
# Double-click this in Finder to start the app. It opens in Terminal, so any problem is
# visible instead of hidden behind a dialog, and Ctrl+C stops the server cleanly.

# This file lives next to run_app.py; resolve from its own location so the repo works
# from wherever it was cloned.
APP_DIR="$(cd "$(dirname "$0")" && pwd -P)"

# Dependencies come from uv.lock. uv creates .venv. Do not use system pip.
find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return
    fi
    for candidate in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return
        fi
    done
}

printf '\n  Flow Colinearity\n  %s\n\n' "----------------------------------------"

UV="$(find_uv)"
if [ -z "$UV" ]; then
    echo "  uv is not installed."
    echo "  Install it from https://docs.astral.sh/uv/ and open this file again."
    echo
    read -r -p "  Press Return to close." _
    exit 1
fi

echo "  Syncing dependencies..."
if ! (cd "$APP_DIR" && "$UV" sync --frozen); then
    echo
    echo "  uv sync failed. From this folder, run:  uv sync"
    echo
    read -r -p "  Press Return to close." _
    exit 1
fi

# First free port from 8600 up.
PORT=""
for p in $(seq 8600 8620); do
    if ! /usr/sbin/lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then PORT=$p; break; fi
done
if [ -z "$PORT" ]; then
    echo "  No free port between 8600 and 8620."
    read -r -p "  Press Return to close." _
    exit 1
fi

echo "  Starting on http://localhost:$PORT"
echo "  Your browser will open automatically."
echo "  Leave this window open while you work; press Ctrl+C here to stop."
echo

(cd "$APP_DIR" && exec env PORT="$PORT" "$UV" run --no-sync python run_app.py) &
SERVER_PID=$!
trap 'echo; echo "  Stopping..."; kill $SERVER_PID 2>/dev/null; exit 0' INT TERM

for _ in $(seq 1 60); do
    if /usr/bin/curl -s -o /dev/null -m 2 "http://localhost:$PORT"; then
        /usr/bin/open "http://localhost:$PORT"
        break
    fi
    kill -0 "$SERVER_PID" 2>/dev/null || break
    sleep 0.5
done

wait "$SERVER_PID"
