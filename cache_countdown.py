#!/usr/bin/env python3
"""
Claude Cache Countdown - Live prompt cache TTL countdown for Claude Code sessions.

Tracks Anthropic's prompt cache TTL (5 minutes by default) and displays a live
countdown so you know exactly when your cache will expire. Most useful when an
agent has stopped and you're deciding whether to continue the conversation.

Supports multiple display backends:
  - Windows Terminal tab titles (default on Windows)
  - Terminal title via ANSI escape codes (default on macOS/Linux)
  - tmux status bar
  - Plain text to stdout (for piping into other tools)

Usage:
    python cache_countdown.py                      # auto-detect display
    python cache_countdown.py --display ansi       # ANSI terminal title
    python cache_countdown.py --display tmux       # tmux status-right
    python cache_countdown.py --display stdout     # plain text output
    python cache_countdown.py --display windows    # Windows Terminal tabs
    python cache_countdown.py --ttl 300            # 5-minute TTL (default)
    python cache_countdown.py --ttl 3600           # 1-hour TTL
    python cache_countdown.py --once               # single update, then exit
"""
import json
import os
import sys
import time
import signal
import argparse
import platform
from pathlib import Path
from datetime import datetime, timezone

# State directory where Claude Code hooks write timer files
STATE_DIR = Path.home() / ".claude" / "state"


# ---------------------------------------------------------------------------
# Data layer (platform-agnostic)
# ---------------------------------------------------------------------------

def read_cache_timers() -> list[dict]:
    """Read all cache-timer-*.json files and return active sessions."""
    sessions = []
    for f in STATE_DIR.glob("cache-timer-*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            ts_str = data.get("timestamp", "")
            if not ts_str:
                continue
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            sessions.append({
                "session_id": data.get("session_id", ""),
                "project": data.get("project", "?"),
                "host_pid": data.get("host_pid", 0),
                "timestamp": ts,
                "file": f,
            })
        except (json.JSONDecodeError, ValueError, OSError):
            continue
    return sessions


def is_process_alive(pid: int) -> bool:
    """Check if a process is still running (cross-platform)."""
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


def format_countdown(remaining: float) -> str:
    """Format seconds remaining as M:SS."""
    if remaining <= 0:
        return "COLD"
    mins = int(remaining) // 60
    secs = int(remaining) % 60
    return f"{mins}:{secs:02d}"


def get_icon(remaining: float, ttl: float) -> str:
    """Get status icon based on remaining time."""
    if remaining <= 0:
        return "\u2744"  # snowflake - cache cold
    ratio = remaining / ttl
    if ratio > 0.5:
        return "\U0001f7e2"  # green
    elif ratio > 0.2:
        return "\U0001f7e1"  # yellow
    return "\U0001f534"  # red


def compute_remaining(session: dict, ttl: float) -> float:
    """Compute seconds remaining for a session."""
    now = datetime.now(timezone.utc)
    ref = session["timestamp"]
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    else:
        ref = ref.astimezone(timezone.utc)
    elapsed = (now - ref).total_seconds()
    return ttl - elapsed


# ---------------------------------------------------------------------------
# Display backends
# ---------------------------------------------------------------------------

class StdoutDisplay:
    """Print countdown to stdout. Useful for piping into other tools."""

    def update(self, sessions_data: list[dict], tick: int):
        lines = []
        for s in sessions_data:
            icon = s["icon"]
            cd = s["countdown"]
            proj = s["project"]
            status = s["status"]
            lines.append(f"{icon} {cd} | {proj}")
        # Clear screen and print
        print("\033[2J\033[H" + "\n".join(lines) if lines else "(no active sessions)", end="", flush=True)

    def restore(self):
        print("\033[2J\033[H", end="", flush=True)


class AnsiTitleDisplay:
    """Set terminal title via ANSI OSC escape sequence. Works on most terminals."""

    def update(self, sessions_data: list[dict], tick: int):
        if not sessions_data:
            return
        # Show the most urgent session in the title
        s = sessions_data[0]
        title = f"{s['icon']} {s['countdown']} | {s['project']} {s['status']}".strip()
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()

    def restore(self):
        sys.stdout.write("\033]0;\007")
        sys.stdout.flush()


class TmuxDisplay:
    """Update tmux status-right with cache countdown."""

    def update(self, sessions_data: list[dict], tick: int):
        if not sessions_data:
            os.system("tmux set-option -q status-right ''")
            return
        parts = []
        for s in sessions_data:
            parts.append(f"{s['icon']} {s['countdown']} {s['project']}")
        status = " | ".join(parts)
        os.system(f"tmux set-option -q status-right '{status}'")

    def restore(self):
        os.system("tmux set-option -qu status-right")


class WindowsTerminalDisplay:
    """Set Windows Terminal tab titles via AttachConsole + SetConsoleTitleW."""

    def __init__(self):
        import ctypes
        self._kernel32 = ctypes.windll.kernel32

    def update(self, sessions_data: list[dict], tick: int):
        updates = []
        for s in sessions_data:
            pid = s.get("host_pid", 0)
            if pid <= 0:
                continue
            status_suffix = f" {s['status']}" if s["stopped"] else ""
            title = f"{s['icon']} {s['countdown']} | {s['project']}{status_suffix}"
            updates.append((pid, title))
        self._set_titles(updates)

    def restore(self):
        # Not much we can do here without knowing original titles
        pass

    def _set_titles(self, updates: list[tuple[int, str]]):
        if not updates:
            return
        k = self._kernel32
        if not k.FreeConsole():
            return
        for pid, title in updates:
            if pid <= 0:
                continue
            try:
                if k.AttachConsole(pid):
                    k.SetConsoleTitleW(title)
                    k.FreeConsole()
            except Exception:
                try:
                    k.FreeConsole()
                except Exception:
                    pass
        k.AttachConsole(-1)  # reattach to parent


def get_display(name: str):
    """Get display backend by name, or auto-detect."""
    if name == "auto":
        if platform.system() == "Windows":
            return WindowsTerminalDisplay()
        elif os.environ.get("TMUX"):
            return TmuxDisplay()
        else:
            return AnsiTitleDisplay()

    backends = {
        "stdout": StdoutDisplay,
        "ansi": AnsiTitleDisplay,
        "tmux": TmuxDisplay,
        "windows": WindowsTerminalDisplay,
    }
    cls = backends.get(name)
    if not cls:
        print(f"Unknown display: {name}. Options: {', '.join(backends)}", file=sys.stderr)
        sys.exit(1)
    return cls()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Live prompt cache TTL countdown for Claude Code sessions"
    )
    parser.add_argument("--ttl", type=int, default=300,
                        help="Cache TTL in seconds (default: 300)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Update interval in seconds (default: 1)")
    parser.add_argument("--once", action="store_true",
                        help="Run once and exit (for testing)")
    parser.add_argument("--display", default="auto",
                        choices=["auto", "stdout", "ansi", "tmux", "windows"],
                        help="Display backend (default: auto-detect)")
    args = parser.parse_args()

    display = get_display(args.display)

    print(f"Cache Countdown started (TTL={args.ttl}s, display={args.display})")
    print(f"Watching: {STATE_DIR / 'cache-timer-*.json'}")
    print("Press Ctrl+C to stop.\n")

    known = set()
    tick = 0

    def shutdown(*_):
        display.restore()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    while True:
        sessions = read_cache_timers()
        sessions_data = []

        for s in sessions:
            pid = s["host_pid"]
            sid = s["session_id"]

            if pid > 0 and not is_process_alive(pid):
                if sid in known:
                    known.discard(sid)
                    try:
                        s["file"].unlink()
                    except OSError:
                        pass
                continue

            remaining = compute_remaining(s, args.ttl)
            sessions_data.append({
                "session_id": sid,
                "project": s["project"],
                "host_pid": pid,
                "stopped": s["stopped"],
                "remaining": remaining,
                "countdown": format_countdown(remaining),
                "icon": get_icon(remaining, args.ttl, s["stopped"], tick),
                "status": get_status(remaining, args.ttl, s["stopped"]),
            })

            if sid not in known:
                known.add(sid)
                state = "STOPPED" if s["stopped"] else "active"
                print(f"  Tracking: {s['project']} (PID={pid}, {state})")

        # Sort: stopped sessions first, then by remaining time ascending
        sessions_data.sort(key=lambda x: (not x["stopped"], x["remaining"]))

        display.update(sessions_data, tick)

        if args.once:
            print(f"\nUpdated {len(sessions_data)} session(s).")
            break

        tick += 1
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
