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
PY="/usr/bin/python3"
PORT_START=8600
PORT_END=8620
PIDFILE="$APP_DIR/.server.pid"
LOG="$APP_DIR/.server.log"

die() {  # show a real dialog -- a .app has no console to print to
    /usr/bin/osascript -e "display alert \"Flow Colinearity\" message \"$1\" as critical" >/dev/null 2>&1
    exit 1
}

[ -x "$PY" ] || die "Python 3 not found at $PY. Install Xcode Command Line Tools:  xcode-select --install"
[ -f "$APP_DIR/run_app.py" ] || die "App files not found at $APP_DIR."

# /usr/bin/python3 is a universal binary, and a child process inherits its parent's
# architecture. Launched from something running under Rosetta it comes up as x86_64,
# and then the arm64 numpy/pandas wheels fail to load with a misleading
# "do not import numpy from its source directory" error. Pin it to native.
#
# Detect the *hardware*, not the current process: under Rosetta `uname -m` reports
# x86_64, so testing that skipped the pinning in exactly the case that needs it.
# hw.optional.arm64 is 1 on Apple Silicon however this script happens to be running.
ARCH=""
if [ -x /usr/bin/arch ] && [ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
    ARCH="/usr/bin/arch -arm64"
fi

$ARCH "$PY" -c "import streamlit, numpy, pandas" >/dev/null 2>&1 || die \
"Python dependencies are missing or unusable for $PY.

Open Terminal and run:
    $PY -m pip install --user streamlit numpy pandas matplotlib seaborn scipy pillow

Details:
$($ARCH "$PY" -c 'import streamlit, numpy, pandas' 2>&1 | tail -n 4)"

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

PORT="$PORT" $ARCH "$PY" "$APP_DIR/run_app.py" >"$LOG" 2>&1 &
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
