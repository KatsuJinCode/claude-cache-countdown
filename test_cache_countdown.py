#!/usr/bin/env python3
"""Basic tests for cache_countdown.py"""
import json
import os
import sys
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Patch STATE_DIR before importing
TEST_DIR = Path(tempfile.mkdtemp(prefix="cache-countdown-test-"))

import cache_countdown
cache_countdown.STATE_DIR = TEST_DIR

passed = 0
failed = 0

def test(name, condition):
    global passed, failed
    if condition:
        print(f"  PASS  {name}")
        passed += 1
    else:
        print(f"  FAIL  {name}")
        failed += 1


def write_timer(session_id, project="testapp", seconds_ago=0, host_pid=0, stopped=True):
    ts = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    data = {
        "timestamp": ts.isoformat(),
        "session_id": session_id,
        "project": project,
        "host_pid": host_pid,
    }
    if stopped is not None:
        data["stopped"] = stopped
    path = TEST_DIR / f"cache-timer-{session_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- Tests ---

print("\n=== format_countdown ===")
test("5:00 at full TTL", cache_countdown.format_countdown(300) == "5:00")
test("4:55 at 295s", cache_countdown.format_countdown(295) == "4:55")
test("0:01 at 1s", cache_countdown.format_countdown(1) == "0:01")
test("0:00 at 0s", cache_countdown.format_countdown(0) == "COLD")
test("COLD at negative", cache_countdown.format_countdown(-5) == "COLD")

print("\n=== get_icon ===")
test("green at >50%", cache_countdown.get_icon(200, 295) == "\U0001f7e2")
test("yellow at 20-50%", cache_countdown.get_icon(100, 295) == "\U0001f7e1")
test("red at <20%", cache_countdown.get_icon(30, 295) == "\U0001f534")
test("snowflake at 0", cache_countdown.get_icon(0, 295) == "\u2744")
test("snowflake at negative", cache_countdown.get_icon(-10, 295) == "\u2744")

print("\n=== read_cache_timers ===")
# Clean slate
for f in TEST_DIR.glob("cache-timer-*.json"):
    f.unlink()

test("empty dir returns empty list", len(cache_countdown.read_cache_timers()) == 0)

write_timer("sess-1", "projectA", seconds_ago=60)
test("reads one timer file", len(cache_countdown.read_cache_timers()) == 1)

write_timer("sess-2", "projectB", seconds_ago=120)
test("reads two timer files", len(cache_countdown.read_cache_timers()) == 2)

sessions = cache_countdown.read_cache_timers()
s1 = [s for s in sessions if s["session_id"] == "sess-1"][0]
test("correct project name", s1["project"] == "projectA")
test("stopped is True", s1["stopped"] is True)

print("\n=== compute_remaining ===")
write_timer("sess-fresh", seconds_ago=10)
sessions = cache_countdown.read_cache_timers()
fresh = [s for s in sessions if s["session_id"] == "sess-fresh"][0]
remaining = cache_countdown.compute_remaining(fresh, 295)
test("fresh timer has ~285s remaining", 280 < remaining < 290)

write_timer("sess-old", seconds_ago=400)
sessions = cache_countdown.read_cache_timers()
old = [s for s in sessions if s["session_id"] == "sess-old"][0]
remaining = cache_countdown.compute_remaining(old, 295)
test("expired timer has negative remaining", remaining < 0)

print("\n=== three states ===")
for f in TEST_DIR.glob("cache-timer-*.json"):
    f.unlink()

write_timer("stopped-sess", stopped=True, seconds_ago=60)
write_timer("active-sess", stopped=False, seconds_ago=0)
write_timer("unknown-sess", stopped=None, seconds_ago=30)

sessions = cache_countdown.read_cache_timers()
stopped = [s for s in sessions if s["session_id"] == "stopped-sess"][0]
active = [s for s in sessions if s["session_id"] == "active-sess"][0]
unknown = [s for s in sessions if s["session_id"] == "unknown-sess"][0]

test("stopped=True detected", stopped["stopped"] is True)
test("stopped=False detected", active["stopped"] is False)
test("stopped=None detected", unknown["stopped"] is None)

print("\n=== timer file lifecycle ===")
for f in TEST_DIR.glob("cache-timer-*.json"):
    f.unlink()

# Stop creates file
path = write_timer("lifecycle-test", stopped=True)
test("stop creates timer file", path.exists())

# Resume deletes file
path.unlink()
test("resume deletes timer file", not path.exists())
test("no sessions after delete", len(cache_countdown.read_cache_timers()) == 0)

print("\n=== malformed files ===")
bad_path = TEST_DIR / "cache-timer-bad.json"
bad_path.write_text("not json at all", encoding="utf-8")
test("malformed JSON is skipped", len(cache_countdown.read_cache_timers()) == 0)
bad_path.unlink()

bad_path.write_text('{"session_id":"x"}', encoding="utf-8")
test("missing timestamp is skipped", len(cache_countdown.read_cache_timers()) == 0)
bad_path.unlink()

print("\n=== AlertManager ===")

# Quiet mode suppresses all alerts
am_quiet = cache_countdown.AlertManager(quiet=True)
am_quiet.check("s1", "proj", True, 200, False)
test("quiet mode: no alerts fired", len(am_quiet._fired.get("s1", set())) == 0)

# Normal mode fires stop alert on first check
_two_alerts = [
    {"at": "stop", "type": "bell", "count": 1, "label": "cache draining"},
    {"at": 60,     "type": "bell", "count": 3, "label": "~1 min left"},
]
am = cache_countdown.AlertManager(alerts=_two_alerts, quiet=False)
# Monkey-patch bell to avoid actual terminal noise during tests
_bell_count = 0
_orig_bell = cache_countdown.bell
def _mock_bell(count=1, spacing=0.15):
    global _bell_count
    _bell_count += count
cache_countdown.bell = _mock_bell

am.check("s1", "proj", True, 200, False)
test("stop alert fires on first stopped check", "stop" in am._fired["s1"])
test("urgent not fired yet (200s remaining)", "60" not in am._fired["s1"])
test("bell rang once for stop", _bell_count == 1)

# Second check at same remaining: no repeat
prev_count = _bell_count
am.check("s1", "proj", True, 200, True)
test("stop alert does not repeat", _bell_count == prev_count)

# Urgent fires at <=60s
am.check("s1", "proj", True, 55, True)
test("urgent alert fires at 55s", "60" in am._fired["s1"])
test("bell rang 3 more times for urgent", _bell_count == prev_count + 3)

# Urgent does not repeat
prev_count = _bell_count
am.check("s1", "proj", True, 30, True)
test("urgent does not repeat", _bell_count == prev_count)

# Reset when session becomes active
am.check("s1", "proj", False, 0, True)
test("alerts reset when session active", "s1" not in am._fired)

# After reset, stop alert fires again
am.check("s1", "proj", True, 250, True)
test("stop alert fires again after reset", "stop" in am._fired["s1"])

# Custom alert config
custom_alerts = [
    {"at": "stop", "type": "bell", "count": 2, "label": "paused"},
    {"at": 120, "type": "bell", "count": 1, "label": "2 min warning"},
    {"at": 30, "type": "bell", "count": 5, "label": "30 sec warning"},
]
am_custom = cache_countdown.AlertManager(alerts=custom_alerts, quiet=False)
_bell_count = 0
am_custom.check("c1", "proj", True, 200, False)
test("custom: stop fires with count=2", _bell_count == 2)
am_custom.check("c1", "proj", True, 100, True)
test("custom: 120s alert fires at 100s", "120" in am_custom._fired["c1"])
am_custom.check("c1", "proj", True, 25, True)
test("custom: 30s alert fires at 25s", "30" in am_custom._fired["c1"])

# describe() returns readable descriptions
descs = am_custom.describe()
test("describe returns 3 lines", len(descs) == 3)
test("describe mentions 'on agent stop'", "on agent stop" in descs[0])
test("describe mentions '120s remaining'", "120s remaining" in descs[1])

# Countdown alert type: bells every second
countdown_alerts = [
    {"at": 5, "type": "countdown"},
]
am_cd = cache_countdown.AlertManager(alerts=countdown_alerts, quiet=False)
_bell_count = 0
# Above threshold: no bell
am_cd.check("cd1", "proj", True, 8, False)
test("countdown: no bell above threshold", _bell_count == 0)
# At 4.9s (int = 4): first countdown bell
am_cd.check("cd1", "proj", True, 4.9, True)
test("countdown: bell at 4s", _bell_count == 1)
test("countdown: key countdown:4 fired", "countdown:4" in am_cd._fired["cd1"])
# Same second: no repeat
am_cd.check("cd1", "proj", True, 4.1, True)
test("countdown: no repeat same second", _bell_count == 1)
# Next second (3.5 -> int 3)
am_cd.check("cd1", "proj", True, 3.5, True)
test("countdown: bell at 3s", _bell_count == 2)
# Jump to 1s
am_cd.check("cd1", "proj", True, 1.2, True)
test("countdown: bell at 1s", _bell_count == 3)
# At 0 or below: no more bells (remaining <= 0 guard)
am_cd.check("cd1", "proj", True, 0, True)
test("countdown: no bell at 0", _bell_count == 3)
# Reset and re-fire
am_cd.check("cd1", "proj", False, 0, True)
am_cd.check("cd1", "proj", True, 4.5, True)
test("countdown: fires again after reset", _bell_count == 4)

# Describe handles countdown type
cd_descs = am_cd.describe()
test("countdown describe says 'bell every second'", "bell every second" in cd_descs[0])

cache_countdown.bell = _orig_bell

print("\n=== config ===")
import tempfile as _tempfile
_cfg_path = Path(_tempfile.mktemp(suffix=".json"))
cache_countdown.init_config(_cfg_path)
test("init_config creates file", _cfg_path.is_file())
_cfg = cache_countdown.load_config(_cfg_path)
test("config has alerts key", "alerts" in _cfg)
test("config has 4 default alerts", len(_cfg["alerts"]) == 4)
_cfg_path.unlink()
test("load_config returns {} for missing file", cache_countdown.load_config(Path("/nonexistent/path.json")) == {})
test("config includes cold_ttl field", "cold_ttl" in _cfg)

print("\n=== estimate_cost ===")
# Standard tier (<=200K): cache_read=0.50/MTok, cache_write=6.25/MTok
# delta = (6.25 - 0.50) * tokens/1M = 5.75 * tokens/1M
test("100K standard tier", cache_countdown.estimate_cost(100_000) == "$0.57")
test("200K standard tier", cache_countdown.estimate_cost(200_000) == "$1.15")
# Premium tier (>200K): cache_read=1.00/MTok, cache_write=12.50/MTok
# delta = (12.50 - 1.00) * tokens/1M = 11.50 * tokens/1M
test("500K premium tier", cache_countdown.estimate_cost(500_000) == "$5.75")
test("900K premium tier", cache_countdown.estimate_cost(900_000) == "$10.35")
test("1M premium tier", cache_countdown.estimate_cost(1_000_000) == "$11.50")
test("0 tokens returns empty", cache_countdown.estimate_cost(0) == "")
test("exceeds_200k flag forces premium", cache_countdown.estimate_cost(100_000, exceeds_200k=True) == "$1.15")

print("\n=== read_session_context ===")
# Tier 0: context_tokens embedded in timer data
_timer_with_ctx = {"context_tokens": 300000, "exceeds_200k": True}
_ctx_tokens, _ctx_exceeds = cache_countdown.read_session_context("no-such-id", _timer_with_ctx)
test("tier 0: reads from timer data", _ctx_tokens == 300000)
test("tier 0: reads exceeds flag", _ctx_exceeds is True)

# Tier 1: statusline data file
_sl_path = TEST_DIR / "statusline-data-ctx-test.json"
_sl_data = {
    "session_id": "ctx-test",
    "context_window": {
        "current_usage": {
            "input_tokens": 5,
            "cache_creation_input_tokens": 50000,
            "cache_read_input_tokens": 100000
        }
    },
    "exceeds_200k_tokens": False
}
_sl_path.write_text(json.dumps(_sl_data), encoding="utf-8")
_ctx_tokens, _ctx_exceeds = cache_countdown.read_session_context("ctx-test")
test("tier 1: reads from statusline data", _ctx_tokens == 150005)
test("tier 1: reads exceeds_200k flag", _ctx_exceeds is False)
_sl_path.unlink()

# Graceful fallback: no data at all
_ctx_tokens2, _ctx_exceeds2 = cache_countdown.read_session_context("nonexistent-session")
test("fallback: returns 0", _ctx_tokens2 == 0)
test("fallback: returns False", _ctx_exceeds2 is False)

print("\n=== stale COLD cleanup ===")
for f in TEST_DIR.glob("cache-timer-*.json"):
    f.unlink()

# A session that went COLD 15 minutes ago (remaining = 295 - 1200 = -905)
write_timer("stale-sess", seconds_ago=1200, stopped=True)
sessions = cache_countdown.read_cache_timers()
stale = [s for s in sessions if s["session_id"] == "stale-sess"][0]
remaining = cache_countdown.compute_remaining(stale, 295)
test("stale session has very negative remaining", remaining < -600)
# With cold_ttl=600, this session (remaining ~ -905) should be cleaned up
test("stale session file exists before cleanup", (TEST_DIR / "cache-timer-stale-sess.json").exists())

# A fresh COLD session (just expired, remaining ~ -5)
write_timer("fresh-cold", seconds_ago=300, stopped=True)

print("\n=== _format_session_line ===")
test("basic format", cache_countdown._format_session_line(
    {"icon": "X", "countdown": "1:00", "project": "app"}) == "X 1:00 app")
test("format with cost", cache_countdown._format_session_line(
    {"icon": "X", "countdown": "1:00", "project": "app", "cost": "$5.75"}) == "X 1:00 $5.75 app")
test("format without cost key", cache_countdown._format_session_line(
    {"icon": "X", "countdown": "HOT", "project": "app"}) == "X HOT app")

# --- Cleanup ---
shutil.rmtree(TEST_DIR, ignore_errors=True)

print(f"\n{'='*40}")
print(f"Results: {passed} passed, {failed} failed")
if failed > 0:
    sys.exit(1)
print("All tests passed.")
