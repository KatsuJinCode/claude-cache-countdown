# UserPromptSubmit hook for Claude Code (Windows / PowerShell 7)
# Clears the stopped state when the user sends a new prompt.
# This tells the ticker the session is active again (cache is being refreshed).
#
# Install: Add to ~/.claude/settings.json under hooks.UserPromptSubmit
param()

$ErrorActionPreference = "Continue"

$hookInput = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($hookInput)) { exit 0 }

try { $data = $hookInput | ConvertFrom-Json } catch { exit 0 }

$sid = $data.session_id
if (-not $sid) { exit 0 }

$timerPath = Join-Path $env:USERPROFILE ".claude\state\cache-timer-$sid.json"
if (-not (Test-Path $timerPath)) { exit 0 }

# Clear stopped state and remove the timer file so the ticker stops showing a countdown
try {
    Remove-Item $timerPath -Force -ErrorAction SilentlyContinue
} catch {}

exit 0
