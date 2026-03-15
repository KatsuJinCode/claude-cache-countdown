#!/usr/bin/env bash
# UserPromptSubmit hook for Claude Code
# Marks the cache timer as active (stopped=false) when the user sends a new prompt.
# This tells the ticker the session is active again (cache is being refreshed).
#
# Creates the timer file if it doesn't exist yet (session started after ticker).
# Also cleans up stale timer files from other sessions sharing the same host_pid.
#
# Install: Add to ~/.claude/settings.json under hooks.UserPromptSubmit
#
# No dependencies beyond bash and standard Unix tools.

set -euo pipefail

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | grep -o '"session_id":"[^"]*"' | head -1 | cut -d'"' -f4)
if [ -z "$SESSION_ID" ]; then
    exit 0
fi

CWD=$(echo "$INPUT" | grep -o '"cwd":"[^"]*"' | head -1 | cut -d'"' -f4)
PROJECT=$(basename "${CWD:-unknown}" 2>/dev/null || echo "unknown")

STATE_DIR="$HOME/.claude/state"
mkdir -p "$STATE_DIR"

TIMER_FILE="$STATE_DIR/cache-timer-${SESSION_ID}.json"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")

# Read existing host_pid if file exists, preserve it
HOST_PID=0
if [ -f "$TIMER_FILE" ]; then
    existing_pid=$(grep -o '"host_pid":[0-9]*' "$TIMER_FILE" 2>/dev/null | grep -o '[0-9]*' || true)
    if [ -n "$existing_pid" ]; then
        HOST_PID=$existing_pid
    fi
    existing_project=$(grep -o '"project":"[^"]*"' "$TIMER_FILE" 2>/dev/null | cut -d'"' -f4 || true)
    if [ -n "$existing_project" ]; then
        PROJECT=$existing_project
    fi
fi

# Write timer file with stopped=false FIRST (before any PID discovery)
printf '{"timestamp":"%s","session_id":"%s","project":"%s","host_pid":%d,"stopped":false}' \
    "$TIMESTAMP" "$SESSION_ID" "$PROJECT" "$HOST_PID" > "$TIMER_FILE"

# Best-effort: discover host PID if not already known
if [ "$HOST_PID" -eq 0 ]; then
    if [ "$(uname -s)" = "Darwin" ]; then
        _pid=$$
        for _ in $(seq 1 10); do
            _ppid=$(ps -o ppid= -p "$_pid" 2>/dev/null | tr -d ' ') || break
            [ -z "$_ppid" ] && break
            _name=$(ps -o comm= -p "$_ppid" 2>/dev/null | tr -d ' ') || break
            case "$_name" in
                *Terminal*|*iTerm*|*alacritty*|*wezterm*|*kitty*)
                    HOST_PID=$_pid; break ;;
            esac
            _pid=$_ppid
        done
    elif [ -d "/proc/$$" ]; then
        _pid=$$
        for _ in $(seq 1 10); do
            _ppid=$(awk '{print $4}' "/proc/$_pid/stat" 2>/dev/null) || break
            [ -z "$_ppid" ] || [ "$_ppid" = "0" ] && break
            _name=$(cat "/proc/$_ppid/comm" 2>/dev/null) || break
            case "$_name" in
                *terminal*|*tmux*|*screen*|*alacritty*|*wezterm*|*kitty*|*konsole*|*gnome-t*)
                    HOST_PID=$_pid; break ;;
            esac
            _pid=$_ppid
        done
    fi

    # Re-write with PID if we found it
    if [ "$HOST_PID" -ne 0 ]; then
        printf '{"timestamp":"%s","session_id":"%s","project":"%s","host_pid":%d,"stopped":false}' \
            "$TIMESTAMP" "$SESSION_ID" "$PROJECT" "$HOST_PID" > "$TIMER_FILE"
    fi
fi

# Clean up stale timer files from other sessions sharing the same host_pid
if [ "$HOST_PID" -ne 0 ]; then
    for f in "$STATE_DIR"/cache-timer-*.json; do
        [ -f "$f" ] || continue
        [ "$(basename "$f")" = "cache-timer-${SESSION_ID}.json" ] && continue
        other_pid=$(grep -o '"host_pid":[0-9]*' "$f" 2>/dev/null | grep -o '[0-9]*' || true)
        if [ "$other_pid" = "$HOST_PID" ]; then
            rm -f "$f" 2>/dev/null
        fi
    done
fi

exit 0
