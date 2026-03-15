# cache-timer-resume.ps1 - UserPromptSubmit hook for Claude Code (Windows / PowerShell 7)
# Clears the stopped state on the cache timer file when the user sends a new prompt.
# This tells the ticker the session is active again (cache is being refreshed).
#
# Creates the timer file if it doesn't exist yet (session started after ticker).
# Also cleans up stale timer files from other sessions sharing the same host_pid,
# which happens when /clear or /reset creates a new session_id for the same tab.
#
# Install: Add to ~/.claude/settings.json under hooks.UserPromptSubmit
param()

$ErrorActionPreference = "Continue"

$hookInput = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($hookInput)) { exit 0 }

try { $data = $hookInput | ConvertFrom-Json } catch { exit 0 }

$sid = $data.session_id
if (-not $sid) { exit 0 }

$stateDir = Join-Path $env:USERPROFILE ".claude\state"
if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }
$timerPath = Join-Path $stateDir "cache-timer-$sid.json"

try {
    # Read existing timer file, or start fresh if session is new
    $ht = @{}
    if (Test-Path $timerPath) {
        $timer = Get-Content $timerPath -Raw | ConvertFrom-Json
        $timer.PSObject.Properties | ForEach-Object { $ht[$_.Name] = $_.Value }
    }

    # Populate project for new sessions
    if (-not $ht["project"]) {
        if ($data.cwd) {
            $ht["project"] = Split-Path -Leaf $data.cwd
        } elseif ($env:CLAUDE_PROJECT_DIR) {
            $ht["project"] = Split-Path -Leaf $env:CLAUDE_PROJECT_DIR
        } else {
            $ht["project"] = "unknown"
        }
    }
    if ($data.cwd) {
        $ht["cwd"] = $data.cwd
    }

    # WRITE IMMEDIATELY before PID walk, which can cold-start WMI and timeout
    $ht["stopped"] = $false
    $ht["timestamp"] = (Get-Date -Format "o")
    $ht["session_id"] = $sid
    $ht.Remove("stopped_at")

    $ht | ConvertTo-Json -Compress | Set-Content $timerPath -Force

    # Best-effort: discover host PID if not already known (child of WindowsTerminal)
    # Expensive on first call (WMI cold start), optional for tab title display only.
    if (-not $ht["host_pid"] -or $ht["host_pid"] -eq 0) {
        try {
            $p = [System.Diagnostics.Process]::GetCurrentProcess()
            for ($i = 0; $i -lt 10; $i++) {
                $ppid = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)" -ErrorAction SilentlyContinue).ParentProcessId
                if (-not $ppid) { break }
                $pp = [System.Diagnostics.Process]::GetProcessById($ppid)
                if ($pp.ProcessName -eq "WindowsTerminal") {
                    $ht["host_pid"] = $p.Id
                    break
                }
                $p = $pp
            }
            # Re-write with PID if we found it
            $ht | ConvertTo-Json -Compress | Set-Content $timerPath -Force
        } catch {}
    }

    # Clean up stale timer files from other sessions sharing the same host_pid.
    # This happens when /clear or /reset creates a new session_id for the same tab.
    $myPid = $ht["host_pid"]
    if ($myPid -and $myPid -ne 0) {
        Get-ChildItem (Join-Path $stateDir "cache-timer-*.json") -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -ne "cache-timer-$sid.json"
        } | ForEach-Object {
            try {
                $other = Get-Content $_.FullName -Raw | ConvertFrom-Json
                if ($other.host_pid -eq $myPid) {
                    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
                }
            } catch {}
        }
    }
} catch {}

exit 0
