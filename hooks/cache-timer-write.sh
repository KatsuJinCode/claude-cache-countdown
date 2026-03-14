#!/usr/bin/env bash
# PostToolUse / Stop hook for Claude Code
# Writes a cache timer file so the countdown ticker knows when the last API activity was.
#
# Install: Add to ~/.claude/settings.json under hooks.PostToolUse and hooks.Stop
#
# This script works on macOS, Linux, and Windows (Git Bash/MSYS).

set -euo pipefail

# Read hook input from stdin
INPUT=$(cat)

# Extract session_id
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "")
if [ -z "$SESSION_ID" ]; then
    exit 0
fi

# Extract project name from cwd
CWD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null || echo "")
PROJECT=$(basename "$CWD" 2>/dev/null || echo "unknown")

# Determine state directory
STATE_DIR="$HOME/.claude/state"
mkdir -p "$STATE_DIR"

TIMER_FILE="$STATE_DIR/cache-timer-${SESSION_ID}.json"

# Detect the hook event from environment or input
# Claude Code sets HOOK_EVENT for the event type
HOOK_EVENT="${HOOK_EVENT:-}"

# Check if this is a Stop event
IS_STOPPED="false"
STOPPED_AT=""
if [ "$HOOK_EVENT" = "Stop" ]; then
    IS_STOPPED="true"
    STOPPED_AT=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")
fi

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")

# Find host PID (the shell process that owns our terminal)
# Walk up the process tree to find a recognizable terminal host
HOST_PID=0
if command -v python3 &>/dev/null; then
    HOST_PID=$(python3 -c "
import os, platform
pid = os.getpid()
try:
    if platform.system() == 'Darwin':
        import subprocess
        for _ in range(10):
            result = subprocess.run(['ps', '-o', 'ppid=', '-p', str(pid)], capture_output=True, text=True)
            ppid = int(result.stdout.strip())
            result2 = subprocess.run(['ps', '-o', 'comm=', '-p', str(ppid)], capture_output=True, text=True)
            name = result2.stdout.strip()
            if any(t in name.lower() for t in ['terminal', 'iterm', 'alacritty', 'wezterm', 'kitty']):
                print(pid)
                break
            pid = ppid
        else:
            print(0)
    elif platform.system() == 'Windows':
        # On Windows (Git Bash), walk up looking for WindowsTerminal
        import ctypes
        # Simplified: just output 0, the PowerShell hook handles Windows better
        print(0)
    else:
        # Linux: walk /proc
        for _ in range(10):
            stat = open(f'/proc/{pid}/stat').read()
            ppid = int(stat.split(')')[1].split()[1])
            comm = open(f'/proc/{ppid}/comm').read().strip()
            if any(t in comm.lower() for t in ['terminal', 'tmux', 'screen', 'alacritty', 'wezterm', 'kitty']):
                print(pid)
                break
            pid = ppid
        else:
            print(0)
except Exception:
    print(0)
" 2>/dev/null || echo "0")
fi

# If we have an existing file and this is NOT a stop event, preserve host_pid if we found 0
if [ "$HOST_PID" = "0" ] && [ -f "$TIMER_FILE" ]; then
    EXISTING_PID=$(python3 -c "import json; print(json.load(open('$TIMER_FILE')).get('host_pid',0))" 2>/dev/null || echo "0")
    HOST_PID="$EXISTING_PID"
fi

# Write timer file
if [ "$IS_STOPPED" = "true" ]; then
    cat > "$TIMER_FILE" <<ENDJSON
{"timestamp":"$TIMESTAMP","session_id":"$SESSION_ID","project":"$PROJECT","host_pid":$HOST_PID,"stopped":true,"stopped_at":"$STOPPED_AT"}
ENDJSON
else
    cat > "$TIMER_FILE" <<ENDJSON
{"timestamp":"$TIMESTAMP","session_id":"$SESSION_ID","project":"$PROJECT","host_pid":$HOST_PID,"stopped":false}
ENDJSON
fi

exit 0
