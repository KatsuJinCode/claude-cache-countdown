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
    python cache_countdown.py --quiet                          # no audible alerts
    python cache_countdown.py --config alerts.json             # custom config file
    python cache_countdown.py --init-config                    # generate starter config
"""
import json
import os
import sys
import time
import signal
import argparse
import platform
import subprocess
import threading
from pathlib import Path
from datetime import datetime, timezone

# State directory where Claude Code hooks write timer files
STATE_DIR = Path.home() / ".claude" / "state"

# Default config file location
CONFIG_PATH = Path.home() / ".claude" / "cache-countdown.json"

# Default alert configuration
DEFAULT_ALERTS = [
    {"at": "stop", "type": "bell", "count": 1, "label": "cache draining"},
    {"at": 60,     "type": "bell", "count": 3, "label": "~1 min left"},
    {"at": 30,     "type": "bell", "count": 5, "label": "30 seconds"},
    {"at": 10,     "type": "countdown"},
]


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

def load_config(path: Path = None) -> dict:
    """Load config from JSON file, falling back to defaults."""
    p = path or CONFIG_PATH
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def init_config(path: Path = None):
    """Write a starter config file with defaults and comments."""
    p = path or CONFIG_PATH
    config = {
        "_comment": "Cache Countdown configuration. See --help for CLI overrides.",
        "cold_ttl": 600,
        "_cold_ttl_comment": "Seconds to keep showing COLD sessions before auto-hiding (default: 600 = 10min)",
        "alerts": [
            {
                "_comment": "Alert when agent stops. type: bell or sound",
                "at": "stop",
                "type": "bell",
                "count": 1,
                "label": "cache draining"
            },
            {
                "_comment": "Urgent alert at 60 seconds remaining",
                "at": 60,
                "type": "bell",
                "count": 3,
                "label": "~1 min left"
            },
            {
                "_comment": "Warning at 30 seconds",
                "at": 30,
                "type": "bell",
                "count": 5,
                "label": "30 seconds"
            },
            {
                "_comment": "Bell every second for final 10 seconds",
                "at": 10,
                "type": "countdown"
            }
        ]
    }
    p.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"Config written to: {p}")
    print("Edit this file to customize alerts. Example with sound files:")
    print('  {"at": 60, "type": "sound", "sound": "C:/path/to/alarm.wav", "label": "~1 min left"}')


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

            # "stopped" field: True = stopped, False = active, None = unknown
            # Files without "stopped" field are from external writers (unknown state)
            stopped_raw = data.get("stopped")
            if stopped_raw is None:
                stopped = None  # unknown
            else:
                stopped = bool(stopped_raw)

            sessions.append({
                "session_id": data.get("session_id", ""),
                "project": data.get("project", "?"),
                "host_pid": data.get("host_pid", 0),
                "timestamp": ts,
                "stopped": stopped,
                "file": f,
                "context_tokens": data.get("context_tokens", 0),
                "exceeds_200k": data.get("exceeds_200k", False),
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


def estimate_cost(context_tokens: int, exceeds_200k: bool = False) -> str:
    """Estimate the cost of a cache miss vs hit for a given context size.

    Returns a short string like '$5.75' representing how much money you lose
    if the cache expires and has to be re-written.

    Args:
        context_tokens: Total input tokens (from statusline data or estimate)
        exceeds_200k: Whether the session exceeds the 200K pricing threshold
    """
    if context_tokens <= 0:
        return ""
    # Opus 4.6 pricing tiers
    if exceeds_200k or context_tokens > 200_000:
        cache_read_per_mtok = 1.00
        cache_write_per_mtok = 12.50
    else:
        cache_read_per_mtok = 0.50
        cache_write_per_mtok = 6.25

    mtokens = context_tokens / 1_000_000
    hit_cost = mtokens * cache_read_per_mtok
    miss_cost = mtokens * cache_write_per_mtok
    delta = miss_cost - hit_cost
    return f"${delta:.2f}"


def read_session_context(session_id: str, timer_data: dict = None) -> tuple[int, bool]:
    """Read context size for a session using a three-tier fallback.

    Tier 0: context_tokens embedded in timer file by stop hook
    Tier 1: statusline-data-{session_id}.json (from statusline wrapper)
    Tier 2: (transcript parsing done in stop hook, result in tier 0)

    Returns (total_input_tokens, exceeds_200k).
    Falls back to (0, False) if no data is available.
    """
    # Tier 0: stop hook already parsed transcript/statusline and embedded it
    if timer_data:
        ctx = timer_data.get("context_tokens", 0)
        if ctx and ctx > 0:
            return ctx, timer_data.get("exceeds_200k", False)

    # Tier 1: statusline data file
    data_file = STATE_DIR / f"statusline-data-{session_id}.json"
    try:
        if data_file.is_file():
            data = json.loads(data_file.read_text(encoding="utf-8"))
            ctx_win = data.get("context_window", {})
            usage = ctx_win.get("current_usage")
            if usage:
                total = (usage.get("cache_creation_input_tokens", 0)
                         + usage.get("cache_read_input_tokens", 0)
                         + usage.get("input_tokens", 0))
                if total > 0:
                    exceeds = data.get("exceeds_200k_tokens", False)
                    return total, exceeds
    except (json.JSONDecodeError, OSError, TypeError):
        pass

    return 0, False


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
# Alert system
# ---------------------------------------------------------------------------

def bell(count=1, spacing=0.15):
    """Send terminal bell character(s)."""
    for i in range(count):
        sys.stdout.write("\a")
        sys.stdout.flush()
        if i < count - 1:
            time.sleep(spacing)


def play_sound(path: str):
    """Play a sound file in the background (non-blocking). Cross-platform."""
    if not os.path.isfile(path):
        return
    try:
        if platform.system() == "Windows":
            # Use PowerShell's SoundPlayer for .wav, or mpv/ffplay as fallback
            if path.lower().endswith(".wav"):
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command",
                     f'(New-Object Media.SoundPlayer "{path}").PlaySync()'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            else:
                # Try mpv, then ffplay for non-wav formats
                for player in ["mpv --no-video --really-quiet", "ffplay -nodisp -autoexit -loglevel quiet"]:
                    cmd = player.split() + [path]
                    try:
                        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return
                    except FileNotFoundError:
                        continue
        elif platform.system() == "Darwin":
            subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # Linux: try paplay, then aplay, then ffplay
            for player in ["paplay", "aplay", "ffplay -nodisp -autoexit -loglevel quiet"]:
                cmd = player.split() + [path]
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except FileNotFoundError:
                    continue
    except OSError:
        pass


class AlertManager:
    """Tracks per-session alert state and fires alerts at configurable thresholds."""

    def __init__(self, alerts=None, quiet=False):
        # Per-session tracking: session_id -> set of fired alert keys
        self._fired: dict[str, set[str]] = {}
        self._alerts = alerts if alerts is not None else DEFAULT_ALERTS
        self._quiet = quiet

    def describe(self) -> list[str]:
        """Return human-readable descriptions of configured alerts."""
        lines = []
        for a in self._alerts:
            trigger = a["at"]
            atype = a.get("type", "bell")
            label = a.get("label", "")
            if trigger == "stop":
                when = "on agent stop"
            else:
                when = f"at {trigger}s remaining"
            if atype == "countdown":
                how = "bell every second"
            elif atype == "bell":
                count = a.get("count", 1)
                how = f"{count}x bell"
            elif atype == "sound":
                how = f"sound: {a.get('sound', '?')}"
            else:
                how = atype
            desc = f"  {how} {when}"
            if label:
                desc += f" ({label})"
            lines.append(desc)
        return lines

    def reset(self, session_id: str):
        """Reset alerts for a session (e.g., when it becomes active again)."""
        self._fired.pop(session_id, None)

    def check(self, session_id: str, project: str, stopped: bool,
              remaining: float, was_known: bool):
        """Check alert conditions and fire if needed."""
        if self._quiet:
            return

        if stopped is not True:
            self.reset(session_id)
            return

        fired = self._fired.setdefault(session_id, set())

        for a in self._alerts:
            trigger = a["at"]
            atype = a.get("type", "bell")

            # Countdown type: bell every second from trigger down to 0
            if atype == "countdown":
                if not isinstance(trigger, (int, float)) or remaining > trigger or remaining <= 0:
                    continue
                # Bell for each whole second we haven't belled yet
                current_sec = int(remaining)
                key = f"countdown:{current_sec}"
                if key in fired:
                    continue
                fired.add(key)
                bell(1)
                # Print first time only
                start_key = f"countdown_started:{trigger}"
                if start_key not in fired:
                    fired.add(start_key)
                    try:
                        print(f"  \U000023f0 {project}: final countdown!")
                    except UnicodeEncodeError:
                        print(f"  [!!!] {project}: final countdown!")
                continue

            # Standard one-shot alerts (bell, sound)
            key = f"{trigger}"

            if key in fired:
                continue

            # Check if this alert should fire
            should_fire = False
            if trigger == "stop":
                should_fire = True  # fires on first check after stop
            elif isinstance(trigger, (int, float)) and remaining <= trigger:
                should_fire = True

            if not should_fire:
                continue

            fired.add(key)
            label = a.get("label", "")

            # Fire the alert
            if atype == "sound":
                sound_path = a.get("sound", "")
                if sound_path:
                    threading.Thread(target=play_sound, args=(sound_path,), daemon=True).start()
            else:
                count = a.get("count", 1)
                bell(count, spacing=0.2)

            # Print notification
            msg = f"{project}: {label}" if label else f"{project}: alert"
            try:
                if trigger == "stop":
                    print(f"  \U0001f514 {msg}")
                else:
                    print(f"  \U0001f6a8 {msg}")
            except UnicodeEncodeError:
                if trigger == "stop":
                    print(f"  [!] {msg}")
                else:
                    print(f"  [!!!] {msg}")


# ---------------------------------------------------------------------------
# Display backends
# ---------------------------------------------------------------------------

# ASCII fallback icons for terminals that can't render emoji
_ICON_FALLBACKS = {
    "\U0001f7e2": "[OK]",    # green circle
    "\U0001f7e1": "[!!]",    # yellow circle
    "\U0001f534": "[!!]",    # red circle
    "\u2744": "[--]",        # snowflake
    "\U0001f525": "[**]",    # fire
    "\u2753": "[??]",        # question mark
}


def _safe_encode(text: str) -> str:
    """Replace emoji with ASCII fallbacks if the terminal can't encode them."""
    try:
        text.encode(sys.stdout.encoding or "utf-8")
        return text
    except (UnicodeEncodeError, LookupError):
        for emoji, fallback in _ICON_FALLBACKS.items():
            text = text.replace(emoji, fallback)
        return text


def _format_session_line(s: dict) -> str:
    """Format a session entry for display, including cost if present."""
    cost = s.get("cost", "")
    if cost:
        line = f"{s['icon']} {s['countdown']} {cost} {s['project']}"
    else:
        line = f"{s['icon']} {s['countdown']} {s['project']}"
    return _safe_encode(line)


class StdoutDisplay:
    """Print countdown to stdout. Useful for piping into other tools."""

    def update(self, sessions_data: list[dict]):
        lines = []
        for s in sessions_data:
            lines.append(_format_session_line(s))
        output = "\n".join(lines) if lines else "(no active sessions)"
        print(f"\033[2J\033[H{output}", end="", flush=True)

    def restore(self):
        print("\033[2J\033[H", end="", flush=True)


class AnsiTitleDisplay:
    """Set terminal title via ANSI OSC escape sequence. Works on most terminals."""

    def update(self, sessions_data: list[dict]):
        if not sessions_data:
            return
        # Show the most urgent session in the title
        s = sessions_data[0]
        title = _format_session_line(s)
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()

    def restore(self):
        sys.stdout.write("\033]0;\007")
        sys.stdout.flush()


class TmuxDisplay:
    """Update tmux status-right with cache countdown."""

    def update(self, sessions_data: list[dict]):
        import subprocess
        if not sessions_data:
            subprocess.run(["tmux", "set-option", "-q", "status-right", ""],
                           capture_output=True)
            return
        parts = []
        for s in sessions_data:
            parts.append(_format_session_line(s))
        status = " | ".join(parts)
        subprocess.run(["tmux", "set-option", "-q", "status-right", status],
                       capture_output=True)

    def restore(self):
        import subprocess
        subprocess.run(["tmux", "set-option", "-qu", "status-right"],
                       capture_output=True)


class WindowsTerminalDisplay:
    """Set Windows Terminal tab titles via AttachConsole + SetConsoleTitleW."""

    def __init__(self):
        import ctypes
        self._kernel32 = ctypes.windll.kernel32

    def update(self, sessions_data: list[dict]):
        updates = []
        for s in sessions_data:
            pid = s.get("host_pid", 0)
            if pid <= 0:
                continue
            title = _format_session_line(s)
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
    parser.add_argument("--ttl", type=int, default=295,
                        help="Cache TTL in seconds (default: 295, slightly under 5min for safety)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Update interval in seconds (default: 1)")
    parser.add_argument("--once", action="store_true",
                        help="Run once and exit (for testing)")
    parser.add_argument("--display", default="auto",
                        choices=["auto", "stdout", "ansi", "tmux", "windows"],
                        help="Display backend (default: auto-detect)")
    parser.add_argument("--quiet", action="store_true",
                        help="Disable all audible alerts")
    parser.add_argument("--config", default=None,
                        help=f"Path to config file (default: {CONFIG_PATH})")
    parser.add_argument("--init-config", action="store_true",
                        help="Generate a starter config file and exit")
    parser.add_argument("--cold-ttl", type=int, default=600,
                        help="Seconds to keep showing COLD sessions before hiding (default: 600 = 10min)")
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else CONFIG_PATH

    if args.init_config:
        init_config(config_path)
        sys.exit(0)

    # Load config file if present
    config = load_config(config_path)
    alert_config = config.get("alerts", DEFAULT_ALERTS)

    cold_ttl = args.cold_ttl if args.cold_ttl != 600 else config.get("cold_ttl", 600)

    display = get_display(args.display)
    alerts = AlertManager(alerts=alert_config, quiet=args.quiet)

    print(f"Cache Countdown started (TTL={args.ttl}s, display={args.display})")
    print(f"Watching: {STATE_DIR / 'cache-timer-*.json'}")
    print("Cost: reads context from statusline data (per-session, live)")
    if args.quiet:
        print("Alerts: disabled (--quiet). Run without --quiet to enable.")
    else:
        print("Alerts:")
        for line in alerts.describe():
            print(line)
        if config_path.is_file():
            print(f"  (config: {config_path})")
        else:
            print(f"  (defaults; run --init-config to customize)")
    print("Press Ctrl+C to stop.\n")

    known = set()

    def shutdown(*_):
        display.restore()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    while True:
        sessions = read_cache_timers()

        # Deduplicate by host_pid: keep only the most recent session per PID.
        # When /clear or /reset creates a new session_id for the same tab,
        # the old timer file lingers and causes flickering between states.
        by_pid: dict[int, dict] = {}
        stale: list[dict] = []
        for s in sessions:
            pid = s["host_pid"]
            if pid <= 0:
                # No PID tracking, keep all
                by_pid.setdefault(id(s), s)
                continue
            if pid in by_pid:
                existing = by_pid[pid]
                if s["timestamp"] > existing["timestamp"]:
                    stale.append(existing)
                    by_pid[pid] = s
                else:
                    stale.append(s)
            else:
                by_pid[pid] = s

        # Remove stale duplicate timer files
        for s in stale:
            try:
                s["file"].unlink()
            except OSError:
                pass
            known.discard(s["session_id"])

        sessions = list(by_pid.values())
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
            stopped = s.get("stopped")

            # If the session claims to be active but the process is gone (pid=0
            # means PID discovery failed), treat it as stopped. Without this,
            # sessions with no PID stay "HOT" forever after the process exits.
            if stopped is False and pid <= 0 and remaining <= 0:
                stopped = True

            # Three states:
            # stopped=True  -> countdown (cache is draining)
            # stopped=False -> HOT (agent is working)
            # stopped=None  -> unknown (no hook has fired yet)
            if stopped is True:
                icon = get_icon(remaining, args.ttl)
                countdown = format_countdown(remaining)
            elif stopped is False:
                icon = "\U0001f525"  # fire - HOT
                countdown = "HOT"
            else:
                icon = "\u2753"  # question mark - unknown
                countdown = "..."

            was_known = sid in known

            entry = {
                "session_id": sid,
                "project": s["project"],
                "host_pid": pid,
                "remaining": remaining,
                "countdown": countdown,
                "icon": icon,
            }
            ctx_tokens, exceeds_200k = read_session_context(sid, s)
            cost = estimate_cost(ctx_tokens, exceeds_200k)
            if cost:
                entry["cost"] = cost
            sessions_data.append(entry)

            if not was_known:
                known.add(sid)
                state = {True: "STOPPED", False: "active", None: "unknown"}[stopped]
                print(f"  Tracking: {s['project']} (PID={pid}, {state})")

            alerts.check(sid, s["project"], stopped, remaining, was_known)

        # Remove stale COLD sessions (expired longer than cold_ttl)
        # and clean up their timer files
        active_data = []
        for s in sessions_data:
            if s["remaining"] < -cold_ttl:
                known.discard(s["session_id"])
                # Find and remove the timer file
                timer_file = STATE_DIR / f"cache-timer-{s['session_id']}.json"
                try:
                    timer_file.unlink()
                except OSError:
                    pass
            else:
                active_data.append(s)
        sessions_data = active_data

        # Sort by remaining time ascending (most urgent first)
        sessions_data.sort(key=lambda x: x["remaining"])

        display.update(sessions_data)

        if args.once:
            print(f"\nUpdated {len(sessions_data)} session(s).")
            break

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
