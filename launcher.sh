#!/bin/bash
# Launcher for the Flow Colinearity app bundle.
#
# Double-clicking the .app runs this. It starts the Streamlit server on a free port,
# waits until it actually answers, opens the browser, then stays alive so that quitting
# the app (Cmd-Q, or Quit from the Dock) also stops the server -- otherwise a stray
# server keeps the port and serves stale code, which is exactly the failure mode that
# wasted an afternoon during development.

# Find the project directory relative to this script, so the repo works wherever it is
# cloned. This file is run from two places: directly (next to run_app.py) and as the
# executable inside "Flow Colinearity.app/Contents/MacOS/", three levels down -- so
# walk upward until run_app.py appears rather than assuming either layout.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd -P)"
APP_DIR="$HERE"
for _ in 1 2 3 4; do
    [ -f "$APP_DIR/run_app.py" ] && break
    APP_DIR="$(dirname "$APP_DIR")"
done
PORT_START=8600
PORT_END=8620
PIDFILE="$APP_DIR/.server.pid"
LOG="$APP_DIR/.server.log"

die() {  # show a real dialog -- a .app has no console to print to
    /usr/bin/osascript -e "display alert \"Flow Colinearity\" message \"$1\" as critical" >/dev/null 2>&1
    exit 1
}

[ -f "$APP_DIR/run_app.py" ] || die "App files not found at $APP_DIR."

# Dependencies come from uv.lock. uv creates .venv on first sync, so this
# launcher does not call system pip. Homebrew Python is PEP 668-locked.
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
UV="$(find_uv)"
[ -n "$UV" ] || die "uv is not installed. Install it from https://docs.astral.sh/uv/ and open the app again."

if ! (cd "$APP_DIR" && "$UV" sync --frozen); then
    die "uv sync failed. Open Terminal in the repo folder and run:  uv sync"
fi

open_browser() { /usr/bin/open "http://localhost:$1" >/dev/null 2>&1; }

# Already running from a previous launch? Just surface it again.
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cut -d: -f1 "$PIDFILE" 2>/dev/null)
    OLD_PORT=$(cut -d: -f2 "$PIDFILE" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        open_browser "$OLD_PORT"
        exit 0
    fi
    rm -f "$PIDFILE"
fi

# First free port in the range.
PORT=""
for p in $(seq $PORT_START $PORT_END); do
    if ! /usr/sbin/lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then PORT=$p; break; fi
done
[ -n "$PORT" ] || die "No free port between $PORT_START and $PORT_END."

(cd "$APP_DIR" && exec env PORT="$PORT" "$UV" run --no-sync python run_app.py) >"$LOG" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID:$PORT" > "$PIDFILE"

cleanup() { kill "$SERVER_PID" 2>/dev/null; rm -f "$PIDFILE"; }
trap cleanup EXIT INT TERM

# Wait for it to actually answer before opening the browser, so the user never lands
# on a "can't connect" page.
for _ in $(seq 1 60); do
    if /usr/bin/curl -s -o /dev/null -m 2 "http://localhost:$PORT"; then
        open_browser "$PORT"
        wait "$SERVER_PID"
        exit 0
    fi
    kill -0 "$SERVER_PID" 2>/dev/null || break
    sleep 0.5
done

die "The server did not start. Last lines of the log:

$(tail -n 12 "$LOG" 2>/dev/null)"
