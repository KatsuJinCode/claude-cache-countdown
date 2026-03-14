#!/usr/bin/env bash
# Stop hook for Claude Code - writes a cache timer file when the agent stops.
# The countdown ticker reads this file to show how long until the cache expires.
#
# Install: Add to ~/.claude/settings.json under hooks.Stop
#
# Works on macOS, Linux, and Windows (Git Bash/MSYS).

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

# State directory
STATE_DIR="$HOME/.claude/state"
mkdir -p "$STATE_DIR"

TIMER_FILE="$STATE_DIR/cache-timer-${SESSION_ID}.json"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")

# Find host PID (the shell process that owns our terminal tab)
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
                print(pid); break
            pid = ppid
        else:
            print(0)
    elif platform.system() == 'Linux':
        for _ in range(10):
            stat = open(f'/proc/{pid}/stat').read()
            ppid = int(stat.split(')')[1].split()[1])
            comm = open(f'/proc/{ppid}/comm').read().strip()
            if any(t in comm.lower() for t in ['terminal', 'tmux', 'screen', 'alacritty', 'wezterm', 'kitty']):
                print(pid); break
            pid = ppid
        else:
            print(0)
    else:
        print(0)
except Exception:
    print(0)
" 2>/dev/null || echo "0")
fi

# Write timer file
cat > "$TIMER_FILE" <<ENDJSON
{"timestamp":"$TIMESTAMP","session_id":"$SESSION_ID","project":"$PROJECT","host_pid":$HOST_PID}
ENDJSON

exit 0
