#!/bin/bash
# Optional (macOS): start NEPSE Desk automatically when you log in, so daily updates never get missed.
# Undo with:  ./install_autostart.sh remove
cd "$(dirname "$0")"
DIR="$(pwd)"
PLIST="$HOME/Library/LaunchAgents/com.nepsedesk.app.plist"

if [ "$1" == "remove" ]; then
  launchctl unload "$PLIST" 2>/dev/null; rm -f "$PLIST"; echo "Autostart removed."; exit 0
fi
if [ ! -x .venv/bin/python ]; then echo "Run ./start.sh once first."; exit 1; fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.nepsedesk.app</string>
  <key>ProgramArguments</key><array><string>$DIR/.venv/bin/python</string><string>$DIR/server.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/data/server.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/server.err.log</string>
</dict></plist>
EOF
launchctl unload "$PLIST" 2>/dev/null
launchctl load "$PLIST"
echo "Done. NEPSE Desk now runs in the background. Open http://localhost:8050 any time."
