# Claude Cache Countdown

Live prompt cache TTL countdown for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions.

With Claude Opus 4.6's **1 million token context window**, prompt caching has never been more important. Anthropic caches your conversation context server-side for 5 minutes. Cache hits cost 90% less. But when your agent stops, that cache is silently draining, and the stakes are real:

**At 500K tokens (a medium session in the premium pricing tier):**
- Cache hit: **$0.50**
- Cache expired (re-write at 1.25x): **$6.25**
- **Being one second late costs you $5.75.**

And there's a pricing cliff: once your context exceeds 200K tokens, the *entire* request is billed at 2x the standard rate ($10/MTok instead of $5/MTok). A deep 900K session that loses its cache pays $11.25 to re-cache what would have cost $0.90 to read.

This tool shows you exactly how much time you have left.

We couldn't find anything else that does this. Prompt caching is well-documented by Anthropic, but as of March 2026 we're not aware of any tooling that provides live cache TTL visibility for Claude Code or other LLM CLI tools.

![Cache countdown in Windows Terminal tabs](img/Screenshot%202026-03-14%20035733.png)

## What it does

- Shows a live countdown when your agent stops and the cache is draining
- Shows **cost at risk** per session (auto-detected from statusline data or transcript)
- **Escalating audible alerts**: bell on stop, triple bell at 1 min, 5x bell at 30s, per-second countdown for final 10s
- Customizable alert thresholds and sound files via config
- Switches to HOT when you send a new message (cache is refreshing)
- Tracks multiple Claude Code sessions simultaneously
- Auto-hides stale sessions after configurable cold TTL
- Supports multiple display backends (terminal titles, tmux, stdout)
- Zero dependencies (Python stdlib only)

## Quick Start

### Automatic install

```bash
git clone https://github.com/KatsuJinCode/claude-cache-countdown.git
cd claude-cache-countdown

# macOS / Linux
bash install.sh

# Windows (PowerShell 7)
pwsh -File install.ps1
```

The installer adds both hooks to your Claude Code settings. Restart Claude Code to load them.

### Manual install

Two hooks:
- **Stop** -- starts the countdown when the agent finishes
- **UserPromptSubmit** -- switches to HOT when you send a new message

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
    ],
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/claude-cache-countdown/hooks/cache-timer-resume.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

**Windows (PowerShell 7):** Use the `.ps1` versions instead:
```json
"command": "pwsh.exe -NoProfile -File C:/path/to/claude-cache-countdown/hooks/cache-timer-write.ps1"
"command": "pwsh.exe -NoProfile -File C:/path/to/claude-cache-countdown/hooks/cache-timer-resume.ps1"
```

Then run the countdown:

```bash
python cache_countdown.py
```

The ticker auto-detects your platform and picks the right display backend.

## How it works

```
Claude Code session is working...     (timer file has stopped=false, shows 🔥 HOT)
    |
    v
Agent stops                           (🔔 bell alert)
    |
    v
Stop hook --------> sets stopped=true, timestamp=now
    |
    v
Ticker ------------> reads timer files every second, shows countdown
    |
    |                "🟢 4:32 | myapp"  -->  "🟡 2:15 | myapp"  -->  "🔴 0:45 | myapp"  -->  "❄️ COLD"
    |                                                                  (🚨 urgent alert at ~1 min)
    v
User sends new message
    |
    v
Resume hook ------> sets stopped=false, countdown switches to 🔥 HOT
```

While the agent is working, every API call resets the cache. The TTL is always full. There's nothing to count down. The countdown only starts when the agent stops and the cache begins draining. Alerts fire at configurable thresholds. Stale COLD sessions auto-hide after 10 minutes.

## Visual states

| Display | Meaning |
|---------|---------|
| `🔥 HOT \| myapp` | Agent is working, cache is always fresh |
| `🟢 4:32 \| myapp` | Agent stopped, cache is fresh, you have time |
| `🟡 2:15 \| myapp` | Agent stopped, cache aging, don't wait too long |
| `🔴 0:45 \| myapp` | Agent stopped, cache about to expire, act now |
| `❄️ COLD \| myapp` | Cache expired |

## Display backends

| Backend | Flag | How it works |
|---------|------|--------------|
| **Windows Terminal** | `--display windows` | Sets each tab's title via Win32 `AttachConsole` + `SetConsoleTitleW`. One ticker process manages all tabs. |
| **ANSI title** | `--display ansi` | Sets terminal title via `\033]0;title\007`. Works on iTerm2, Alacritty, WezTerm, Kitty, most modern terminals. |
| **tmux** | `--display tmux` | Updates `status-right` with countdown for all sessions. |
| **stdout** | `--display stdout` | Prints countdown to stdout. Pipe into other tools. |
| **auto** | (default) | Windows Terminal on Windows, tmux if `$TMUX` is set, ANSI otherwise. |

## Alerts

When the ticker starts, it shows what alerts are configured:

```
Cache Countdown started (TTL=295s, display=auto)
Watching: ~/.claude/state/cache-timer-*.json
Cost: auto-detected from statusline data or transcript on stop
Alerts:
  1x bell on agent stop (cache draining)
  3x bell at 60s remaining (~1 min left)
  5x bell at 30s remaining (30 seconds)
  bell every second at 10s remaining
  (defaults; run --init-config to customize)
```

Default alerts (escalating urgency):
- **On agent stop**: single bell, cache is draining
- **At 1 minute**: triple bell
- **At 30 seconds**: 5x bell
- **Final 10 seconds**: bell every second (countdown)

Use `--quiet` to disable alerts. The ticker will tell you how to re-enable them.

### Custom alerts

Generate a config file:

```bash
python cache_countdown.py --init-config
```

This creates `~/.claude/cache-countdown.json` with the defaults:

```json
{
  "alerts": [
    {"at": "stop", "type": "bell", "count": 1, "label": "cache draining"},
    {"at": 60, "type": "bell", "count": 3, "label": "~1 min left"},
    {"at": 30, "type": "bell", "count": 5, "label": "30 seconds"},
    {"at": 10, "type": "countdown"}
  ]
}
```

Each alert has:
- `at`: when to fire. `"stop"` for when the agent stops, or a number (seconds remaining)
- `type`: `"bell"` (terminal bell), `"sound"` (play a file), or `"countdown"` (bell every second from this point)
- `count`: how many bells (for `"bell"` type)
- `sound`: path to a sound file (for `"sound"` type)
- `label`: text shown in the terminal when the alert fires

Example with custom sounds and multiple thresholds:

```json
{
  "alerts": [
    {"at": "stop", "type": "sound", "sound": "C:/sounds/ding.wav", "label": "cache draining"},
    {"at": 120, "type": "bell", "count": 1, "label": "2 min warning"},
    {"at": 60, "type": "sound", "sound": "C:/sounds/alarm.wav", "label": "1 min left"},
    {"at": 30, "type": "bell", "count": 5, "label": "last chance"}
  ]
}
```

Sound playback is cross-platform: Windows (SoundPlayer/.wav, mpv/ffplay for other formats), macOS (afplay), Linux (paplay, aplay, ffplay).

## Options

```
--ttl 295       Cache TTL in seconds (default: 295, 5s safety margin under the 5min cache)
--ttl 3600      Use if your API calls use the 1-hour cache ("ttl": "1h")
--interval 1    Update frequency in seconds (default: 1)
--once          Run once and exit (for testing or scripting)
--display X     Choose display backend (auto, windows, ansi, tmux, stdout)
--quiet         Disable all audible alerts
--config PATH   Use a custom config file (default: ~/.claude/cache-countdown.json)
--init-config   Generate a starter config file and exit
--cold-ttl 600  Seconds to keep showing COLD sessions before auto-hiding (default: 600 = 10min)
```

### Cost at risk

The ticker automatically shows how much money is at stake if the cache expires. It reads the actual context size from your session using a three-tier fallback:

1. **Statusline data** (best): if you have a statusline wrapper that writes `~/.claude/state/statusline-data-{session_id}.json`, the ticker reads live token counts from it
2. **Transcript parsing**: the stop hook reads the last few lines of the session transcript (`~/.claude/projects/.../session_id.jsonl`) to extract token usage
3. **Graceful fallback**: if neither is available, cost is simply not shown

The cost appears next to the countdown: `🔴 0:45 $5.75 myapp`

This is the delta between a cache hit and a cache miss (the extra money you pay because you were late). At 500K tokens on the premium tier, a cache hit costs $0.50 but a miss forces a $6.25 re-write, so you're risking $5.75.

### Stale session cleanup

COLD sessions auto-hide after 10 minutes (configurable via `--cold-ttl`). Their timer files are cleaned up automatically so you don't accumulate clutter from finished sessions.

---

The default TTL is 295 seconds (4:55) rather than 300 (5:00). The timer starts from when we detect the stop event, not from the last API call. The 5-second buffer means you'll never see "0:01" and think you have time when the cache has already expired.

## Timer file format

The Stop hook writes one JSON file per session to `~/.claude/state/cache-timer-{session_id}.json`:

```json
{
  "timestamp": "2026-03-14T10:35:00.000Z",
  "session_id": "e861c4a2-5b5a-4eb3-99cd-e71c9e6b6983",
  "project": "myapp",
  "host_pid": 12345,
  "stopped": true
}
```

- `timestamp`: when the state last changed
- `stopped`: `true` = cache draining (show countdown), `false` = agent working (show HOT)
- `host_pid`: PID of the terminal tab's process (optional, used for Windows Terminal tab titles)

The ticker calculates `remaining = 295 - (now - timestamp)`.

The UserPromptSubmit hook sets `stopped` to `false` when the user resumes. The Stop hook sets it back to `true` when the agent finishes. Stale files are cleaned up automatically after the cold TTL expires.

## Adapting to your environment

The tool is split into two independent pieces: **hooks** (write/update JSON files) and **ticker** (read JSON files, display, alerts, cost). They communicate through a simple file format. You can swap either side without touching the other.

### Writing your own display backend

The `StdoutDisplay` class in `cache_countdown.py` is about 10 lines and shows the minimal implementation. The `--display stdout` flag outputs plain text you can pipe:

```bash
python cache_countdown.py --display stdout | your-tool
python cache_countdown.py --once --display stdout
```

### Terminal compatibility

| Terminal | Recommended display |
|----------|---------------------|
| **Windows Terminal** | `--display windows` |
| **iTerm2 / Alacritty / WezTerm / Kitty** | `--display ansi` |
| **tmux** | `--display tmux` |
| **VS Code terminal / macOS Terminal.app** | `--display ansi` |
| **GNU Screen** | `--display stdout` piped to hardstatus |

### Writing your own hooks

Two actions:

1. **On session stop:** Write `~/.claude/state/cache-timer-{session_id}.json` with `stopped` set to `true` and `timestamp` set to now.
2. **On user prompt:** Update the same file with `stopped` set to `false` and `timestamp` set to now.

Optional fields:
- `host_pid`: enables Windows Terminal tab title display. Set to `0` if not needed.
- `cwd`: the session's working directory. Enables cost display via transcript parsing.

### Using without hooks

You can create and update timer files yourself from any script:

```bash
# Start a countdown (agent stopped, cache draining)
echo '{"timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%S.000Z)'","session_id":"manual","project":"myapp","host_pid":0,"stopped":true,"cwd":"'$PWD'"}' \
  > ~/.claude/state/cache-timer-manual.json

# Mark as active (agent working, cache refreshing)
echo '{"timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%S.000Z)'","session_id":"manual","project":"myapp","host_pid":0,"stopped":false,"cwd":"'$PWD'"}' \
  > ~/.claude/state/cache-timer-manual.json
```

## Prompt caching reference

| TTL | Write cost | Read cost | How to use |
|-----|-----------|-----------|------------|
| 5 minutes (default) | 1.25x base | 0.1x base | Claude Code uses this automatically |
| 1 hour (opt-in) | 2x base | 0.1x base | Requires `"ttl": "1h"` in API call |

### Opus 4.6 pricing tiers

Opus 4.6 has a **pricing cliff at 200K tokens**. If your context exceeds 200K by even one token, the entire request is billed at the premium rate.

| Tier | Input | Cache write (1.25x) | Cache read (0.1x) |
|------|-------|--------------------|--------------------|
| Standard (up to 200K) | $5.00/MTok | $6.25/MTok | $0.50/MTok |
| Premium (200K to 1M) | $10.00/MTok | $12.50/MTok | $1.00/MTok |

### What a cache miss costs

| Context size | Tier | Cache hit | Cache miss (re-write) | Cost of being late |
|-------------|------|-----------|----------------------|-------------------|
| 100K | Standard | $0.05 | $0.63 | $0.58 |
| 200K | Standard | $0.10 | $1.25 | $1.15 |
| 201K | **Premium** | $0.20 | $2.51 | **$2.31** |
| 500K | Premium | $0.50 | $6.25 | **$5.75** |
| 900K | Premium | $0.90 | $11.25 | **$10.35** |
| 1M | Premium | $1.00 | $12.50 | **$11.50** |

Note the jump from 200K to 201K: crossing the threshold doubles the cost of the entire request, not just the overflow.

These numbers reflect **input token costs only**, which is what prompt caching affects. Output tokens ($25-37.50/MTok) are billed the same regardless of cache state.

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
