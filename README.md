# Claude Cache Countdown

Live prompt cache TTL countdown for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions.

Anthropic's prompt caching stores your conversation context server-side for **5 minutes**. Cache hits cost 90% less and respond faster. But when your agent stops and you're thinking about what to do next, the cache is silently draining. If it expires, your next message pays full price.

This tool shows you exactly how much time you have left.

As far as we can tell, **nothing else does this.** We searched extensively for existing tools, extensions, or projects that provide any kind of live cache state or TTL visibility for Claude Code (or any LLM CLI). The closest thing is an [empty GitHub repo](https://github.com/1amthis/cache-guard) that claimed the concept but never shipped code. Prompt caching is well-documented by Anthropic, but nobody has built tooling around cache *liveness* until now.

![Cache countdown in Windows Terminal tabs](img/Screenshot%202026-03-14%20035733.png)

## What it does

- Tracks all active Claude Code sessions
- Shows a live countdown of cache TTL remaining
- **Flashes alerts** when an agent has stopped and the cache is draining
- Cleans up automatically when sessions end
- Supports multiple display backends (terminal titles, tmux, stdout)

## Quick Start

### 1. Install the hook

You only need the **Stop** hook. While the agent is working, every API call resets the cache, so the TTL is always full. The countdown only starts when the agent stops and the cache begins draining.

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
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
Claude Code session is working...  (cache is always live, nothing to show)
    |
    v
Agent stops
    |
    v
Stop hook fires -----> writes ~/.claude/state/cache-timer-{session_id}.json
    |                       { "timestamp": "...", "project": "myapp",
    |                         "host_pid": 12345 }
    v
cache_countdown.py --> reads timer files every second
    |                  calculates remaining TTL from timestamp
    |                  updates your terminal with countdown
    v
Tab title / tmux / stdout:  "🟢 4:32 | myapp"  -->  "🔴 0:45 | myapp"  -->  "❄️ COLD"
```

### Why only the Stop hook?

While the agent is working, every API call resets the cache. The TTL is always full. There's nothing to count down. The countdown only starts when the agent stops and the cache begins draining. The appearance of the countdown IS the alert that your cache is at risk.

### Visual states

| Display | Meaning |
|---------|---------|
| `🟢 4:32 \| myapp` | Cache is fresh, you have time |
| `🟡 2:15 \| myapp` | Cache aging, don't wait too long |
| `🔴 0:45 \| myapp` | Cache about to expire, act now |
| `❄️ COLD \| myapp` | Cache expired |

## Timer file format

The Stop hook writes one JSON file per session to `~/.claude/state/cache-timer-{session_id}.json`:

```json
{
  "timestamp": "2026-03-14T10:35:00.000Z",
  "session_id": "e861c4a2-5b5a-4eb3-99cd-e71c9e6b6983",
  "project": "myapp",
  "host_pid": 12345
}
```

That's it. `timestamp` is when the agent stopped (i.e., when the cache started draining). The ticker calculates `remaining = 300 - (now - timestamp)`.

## Adapting to your environment

The tool is deliberately split into two independent pieces: **hooks** (write JSON files) and **display** (read JSON files). They communicate through a simple file format. You can swap either side without touching the other.

### Writing your own display backend

The data contract is one JSON file per session at `~/.claude/state/cache-timer-{session_id}.json`. Your display just needs to:

1. Poll (glob) for `cache-timer-*.json` files
2. Parse the JSON
3. Calculate: `remaining = TTL - (now - timestamp)`
4. Render however you want

The `StdoutDisplay` class in `cache_countdown.py` is ~10 lines and shows the minimal implementation. The `--display stdout` flag outputs plain text you can pipe:

```bash
# Pipe into a menu bar tool
python cache_countdown.py --display stdout | your-menubar-tool

# Use in a shell prompt (fish/zsh/bash)
python cache_countdown.py --once --display stdout

# Feed a Stream Deck, Rainmeter widget, OBS overlay, etc.
python cache_countdown.py --display stdout --interval 1 | your-tool
```

### Terminal-specific notes

| Terminal | Recommended approach |
|----------|---------------------|
| **Windows Terminal** | `--display windows` (built-in). Uses Win32 `AttachConsole` to set each tab's title from a single external process. |
| **iTerm2** | `--display ansi` works. iTerm2 also supports [proprietary escape codes](https://iterm2.com/documentation-escape-codes.html) for badges and tab colors if you want richer display. |
| **Alacritty / WezTerm / Kitty** | `--display ansi` works out of the box. |
| **tmux** | `--display tmux` sets `status-right`. You can also use `--display stdout` and pipe into a custom tmux status script for more control. |
| **Screen** | Use `--display stdout` and read it from a hardstatus script. |
| **VS Code terminal** | `--display ansi` works for the terminal title. |
| **macOS Terminal.app** | `--display ansi` works. Terminal.app respects OSC title sequences. |

### Writing your own hook

If you use a different shell or want to integrate with an existing hook system, the hook just needs to write a JSON file on two events:

**On any tool use (PostToolUse):** Write the file with `stopped: false` and the current timestamp. This tells the ticker "the session is alive, cache was just refreshed."

**On session stop (Stop):** Update the file with `stopped: true` and `stopped_at` set to now. This is the critical event that starts the real countdown.

The `host_pid` field is optional but enables the Windows Terminal display backend. It should be the PID of the process that owns the terminal tab (the direct child of your terminal emulator in the process tree). If you set it to `0`, the `ansi`, `tmux`, and `stdout` backends still work fine.

### Using without hooks at all

If you don't want to install hooks, you can create the timer files yourself from any script:

```bash
# Mark a session as stopped (start the countdown)
echo '{"timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%S.000Z)'","session_id":"my-session","project":"myapp","host_pid":0,"stopped":true,"stopped_at":"'$(date -u +%Y-%m-%dT%H:%M:%S.000Z)'"}' > ~/.claude/state/cache-timer-my-session.json

# The ticker will pick it up within 1 second
```

### Ideas for custom displays

- **macOS menu bar** (e.g., with [rumps](https://github.com/jaredks/rumps) or SwiftBar)
- **Stream Deck button** (poll the JSON, update button text/color)
- **Browser extension** (read files via native messaging)
- **Desktop widget** (Rainmeter on Windows, Conky on Linux)
- **OBS overlay** (for streaming coding sessions)
- **Slack/Discord status** (update your status with cache state)
- **Home Assistant** (trigger automations when cache expires)

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
