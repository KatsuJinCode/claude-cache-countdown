# Claude Cache Countdown

Live prompt cache TTL countdown for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions.

Anthropic's prompt caching stores your conversation context server-side for **5 minutes**. Cache hits cost 90% less and respond faster. But when your agent stops and you're thinking about what to do next, the cache is silently draining. If it expires, your next message pays full price.

This tool shows you exactly how much time you have left.

![demo](https://img.shields.io/badge/status-works_on_my_machine-brightgreen)

## What it does

- Tracks all active Claude Code sessions
- Shows a live countdown of cache TTL remaining
- **Flashes alerts** when an agent has stopped and the cache is draining
- Cleans up automatically when sessions end
- Supports multiple display backends (terminal titles, tmux, stdout)

## Quick Start

### 1. Install the hooks

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/claude-cache-countdown/hooks/cache-timer-write.sh",
            "timeout": 5
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/claude-cache-countdown/hooks/cache-timer-write.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

**Windows (PowerShell 7):** Use `cache-timer-write.ps1` instead:
```json
"command": "pwsh.exe -NoProfile -File C:/path/to/claude-cache-countdown/hooks/cache-timer-write.ps1"
```

### 2. Run the countdown

```bash
python cache_countdown.py
```

That's it. The ticker auto-detects your platform and picks the right display.

## Display Backends

| Backend | Flag | How it works |
|---------|------|--------------|
| **Windows Terminal** | `--display windows` | Sets each tab's title via Win32 `AttachConsole` + `SetConsoleTitleW`. One ticker manages all tabs. |
| **ANSI title** | `--display ansi` | Sets terminal title via `\033]0;title\007`. Works on iTerm2, Alacritty, WezTerm, Kitty, most modern terminals. |
| **tmux** | `--display tmux` | Updates `status-right` with countdown for all sessions. |
| **stdout** | `--display stdout` | Prints countdown to stdout. Pipe it into whatever you want. |
| **auto** | (default) | Windows Terminal on Windows, tmux if `$TMUX` is set, ANSI otherwise. |

## Options

```
--ttl 300       Cache TTL in seconds (default: 300 = 5 minutes)
--ttl 3600      Use 1-hour TTL if your API calls use "ttl": "1h"
--interval 1    Update frequency in seconds (default: 1)
--once          Run once and exit (for testing or scripting)
```

## How it works

```
Claude Code session
    |
    v
PostToolUse hook fires -----> writes ~/.claude/state/cache-timer-{session_id}.json
    |                              { "timestamp": "...", "project": "myapp",
    |                                "host_pid": 12345, "stopped": false }
    v
Agent stops
    |
    v
Stop hook fires ------------> updates file: { "stopped": true, "stopped_at": "..." }
    |
    v
cache_countdown.py ----------> reads timer files every second
    |                          calculates remaining TTL
    |                          updates display with countdown
    v
Tab title / tmux / stdout:  "🟢 4:32 | myapp"  -->  "🔴 0:45 | myapp ACT NOW"
```

### Key insight

During active work, the cache resets on every API call, so the countdown is informational. **The countdown only truly matters after the agent stops.** That's when the tool flashes alerts to grab your attention.

### Visual states

| State | Display | Meaning |
|-------|---------|---------|
| `🟢 4:32 \| myapp` | Steady green | Cache is fresh, agent is working |
| `🟡 2:15 \| myapp` | Steady yellow | Cache aging, agent still working |
| `🔴 0:45 \| myapp` | Steady red | Cache low, agent still working |
| `🟢/⏳ 4:32 \| myapp WAITING` | Flashing | Agent stopped, plenty of time |
| `🟡/🔴 2:15 \| myapp WAITING` | Flashing | Agent stopped, act soon |
| `🔴/⚠️ 0:45 \| myapp ACT NOW` | Flashing | Agent stopped, cache about to expire |
| `❄️ COLD \| myapp` | Steady | Cache expired |

## Timer file format

The hooks write JSON files to `~/.claude/state/cache-timer-{session_id}.json`:

```json
{
  "timestamp": "2026-03-14T10:30:00.000Z",
  "session_id": "e861c4a2-5b5a-4eb3-99cd-e71c9e6b6983",
  "project": "myapp",
  "host_pid": 12345,
  "stopped": false
}
```

When the agent stops:

```json
{
  "timestamp": "2026-03-14T10:30:00.000Z",
  "session_id": "e861c4a2-5b5a-4eb3-99cd-e71c9e6b6983",
  "project": "myapp",
  "host_pid": 12345,
  "stopped": true,
  "stopped_at": "2026-03-14T10:35:00.000Z"
}
```

### Building your own display

The data layer is simple: poll the JSON files, calculate `remaining = TTL - (now - reference_time)`, display however you want. The `--display stdout` backend is a good starting point. You could pipe it into:

- A menu bar app (macOS)
- A Stream Deck button
- A browser extension
- A Discord bot
- A desktop widget
- Literally anything that can read JSON files

## Prompt caching reference

| TTL | Write cost | Read cost | How to use |
|-----|-----------|-----------|------------|
| 5 minutes (default) | 1.25x base | 0.1x base | Claude Code uses this automatically |
| 1 hour (opt-in) | 2x base | 0.1x base | Requires `"ttl": "1h"` in API call |

- Cache reads are 90% cheaper than uncached input
- Each API call that hits the cache resets the TTL timer
- Cache hits improve latency (faster time-to-first-token)
- Cache hits don't count against rate limits
- For Claude Max subscribers: cost is flat-rate, but cache still affects latency and rate limits

See [Anthropic's prompt caching docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) for details.

## Requirements

- Python 3.10+
- Claude Code CLI with hooks support
- No external dependencies (stdlib only)

## License

MIT
