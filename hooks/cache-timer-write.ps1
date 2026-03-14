# cache-timer-stop.ps1 - Stop hook for Claude Code (Windows / PowerShell 7)
# Marks the cache timer file as "stopped" so the external ticker knows
# the cache is now genuinely draining and should show the countdown.
#
# Self-sufficient: discovers host PID independently so sessions WITHOUT
# a status line wrapper still get tracked.
#
# Install: Add to ~/.claude/settings.json under hooks.Stop
param()

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$hookInput = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($hookInput)) { exit 0 }

try {
    $data = $hookInput | ConvertFrom-Json
} catch { exit 0 }

$sid = $data.session_id
if (-not $sid) { exit 0 }

$stateDir = Join-Path $env:USERPROFILE ".claude\state"
if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }
$cacheTimerPath = Join-Path $stateDir "cache-timer-$sid.json"

# Read existing timer file if present
$timerData = @{}
if (Test-Path $cacheTimerPath) {
    try {
        $existing = Get-Content $cacheTimerPath -Raw | ConvertFrom-Json
        $existing.PSObject.Properties | ForEach-Object { $timerData[$_.Name] = $_.Value }
    } catch {
        $timerData = @{}
    }
}

# Discover host PID if not already known (child of WindowsTerminal)
if (-not $timerData["host_pid"] -or $timerData["host_pid"] -eq 0) {
    try {
        $p = [System.Diagnostics.Process]::GetCurrentProcess()
        for ($i = 0; $i -lt 10; $i++) {
            $ppid = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)" -ErrorAction SilentlyContinue).ParentProcessId
            if (-not $ppid) { break }
            $pp = [System.Diagnostics.Process]::GetProcessById($ppid)
            if ($pp.ProcessName -eq "WindowsTerminal") {
                $timerData["host_pid"] = $p.Id
                break
            }
            $p = $pp
        }
    } catch {}
}

# Discover project name if not already known
if (-not $timerData["project"]) {
    if ($data.cwd) {
        $timerData["project"] = Split-Path -Leaf $data.cwd
    } elseif ($env:CLAUDE_PROJECT_DIR) {
        $timerData["project"] = Split-Path -Leaf $env:CLAUDE_PROJECT_DIR
    } else {
        $timerData["project"] = "unknown"
    }
}

# Mark as stopped - timestamp is NOW (when the cache starts draining)
$timerData["stopped"] = $true
$timerData["timestamp"] = (Get-Date -Format "o")
$timerData["session_id"] = $sid

$timerData | ConvertTo-Json -Compress | Set-Content $cacheTimerPath -Force

exit 0
