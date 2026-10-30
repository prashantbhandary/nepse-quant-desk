#!/bin/bash
# Start NEPSE Desk. First run installs everything (2-3 min), then downloads data and trains the model (3-5 min).
cd "$(dirname "$0")"
PORT=${PORT:-8050}
URL="http://localhost:$PORT"
LOG=data/app.log
mkdir -p data

# pick the newest Python 3 available (3.11+ enables live NEPSE prices)
PY=""
for v in python3.13 python3.12 python3.11 python3; do
  if command -v $v >/dev/null 2>&1; then PY=$v; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3 is not installed. Install it from https://www.python.org/downloads/ and run this again."
  exit 1
fi

# is something already listening on the port?
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  if curl -fsS -m 3 -o /dev/null "http://127.0.0.1:$PORT/api/status" 2>/dev/null; then
    echo "NEPSE Desk is already running at $URL (maybe started by ./install_autostart.sh). Opening it."
    open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null
    echo "To start a second copy on another port: PORT=$((PORT + 1)) ./start.sh"
    exit 0
  fi
  echo "Port $PORT is already in use by another program."
  echo "Close that program, or start NEPSE Desk on another port:  PORT=$((PORT + 1)) ./start.sh"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Creating the Python environment ($($PY --version 2>&1))…"
  $PY -m venv .venv || { echo "Could not create virtual environment"; exit 1; }
fi
source .venv/bin/activate
if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  echo "Installing packages (first time, 2–3 min)…"
  PIPLOG=data/pip-install.log
  python -m pip install --upgrade pip >"$PIPLOG" 2>&1
  # show one line per package as progress; the full output stays in the log
  python -m pip install -r requirements.txt 2>&1 | tee -a "$PIPLOG" | grep --line-buffered -E "^(Collecting|Successfully installed)" | while IFS= read -r l; do echo "  $l"; done
  if [ "${PIPESTATUS[0]}" -ne 0 ]; then
    echo ""
    echo "Package installation FAILED. pip said:"
    echo "------------------------------------------------------------"
    grep -v -E "^(Collecting|  Downloading|  Using cached|Requirement already)" "$PIPLOG" | tail -40
    echo "------------------------------------------------------------"
    echo "Full log: $PIPLOG. Fix the problem above (often: no internet, or an old Python) and run ./start.sh again."
    exit 1
  fi
  touch .venv/.installed
fi

echo "Starting server — first launch can take 1–2 minutes, please wait…"
python server.py &
PID=$!

stop() {
  trap - INT TERM
  if kill -0 "$PID" 2>/dev/null; then
    echo ""
    echo "Stopping NEPSE Desk…"
    kill "$PID" 2>/dev/null
    wait "$PID" 2>/dev/null
  fi
  exit "${1:-0}"
}
# a background job ignores Ctrl+C in a script, so forward it as TERM
trap stop INT TERM

died() {
  echo ""
  echo "The server stopped while starting. Last 30 lines of $LOG:"
  echo "------------------------------------------------------------"
  tail -30 "$LOG"
  echo "------------------------------------------------------------"
  exit 1
}

SECONDS=0
until curl -fsS -m 2 -o /dev/null "http://127.0.0.1:$PORT/api/status" 2>/dev/null; do
  kill -0 "$PID" 2>/dev/null || died
  if [ "$SECONDS" -ge 240 ]; then
    echo "The server did not respond within 4 minutes. Last 30 lines of $LOG:"
    tail -30 "$LOG"
    stop 1
  fi
  [ "$SECONDS" -ge 20 ] && [ $((SECONDS % 20)) -lt 2 ] && echo "  still starting… (${SECONDS}s, this is normal on the first launch)"
  sleep 2
done

echo ""
echo "Ready: $URL   (leave this window open; press Ctrl+C to stop)"
echo ""
open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null

wait "$PID"
CODE=$?
trap - INT TERM
echo "NEPSE Desk stopped (exit code $CODE). See $LOG for details."
exit $CODE
