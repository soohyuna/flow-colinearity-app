#!/bin/bash
# Double-click this in Finder to start the app. It opens in Terminal, so any problem is
# visible instead of hidden behind a dialog, and Ctrl+C stops the server cleanly.

# This file lives next to run_app.py; resolve from its own location so the repo works
# from wherever it was cloned.
APP_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PY="/usr/bin/python3"

# /usr/bin/python3 is a universal binary and inherits its parent's architecture. Pin it
# to native arm64, or an x86_64 parent makes the arm64 numpy wheel fail to load with a
# confusing "don't import numpy from its source directory" message.
# Test the hardware, not the process: under Rosetta `uname -m` says x86_64.
ARCH=""
if [ -x /usr/bin/arch ] && [ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
    ARCH="/usr/bin/arch -arm64"
fi

printf '\n  Flow Colinearity\n  %s\n\n' "----------------------------------------"

if ! $ARCH "$PY" -c "import streamlit, numpy, pandas, matplotlib, seaborn, scipy, PIL" 2>/dev/null; then
    echo "  Missing Python packages. Install them with:"
    echo
    echo "      $PY -m pip install --user streamlit numpy pandas matplotlib seaborn scipy pillow"
    echo
    echo "  Details:"
    $ARCH "$PY" -c "import streamlit, numpy, pandas, matplotlib, seaborn, scipy, PIL" 2>&1 | tail -n 5 | sed 's/^/    /'
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

PORT="$PORT" $ARCH "$PY" "$APP_DIR/run_app.py" &
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
