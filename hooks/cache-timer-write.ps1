# Stop hook for Claude Code (Windows / PowerShell 7)
# Writes a cache timer file when the agent stops.
# The countdown ticker reads this file to show how long until the cache expires.
#
# Install: Add to ~/.claude/settings.json under hooks.Stop
param()

$ErrorActionPreference = "Continue"

$hookInput = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($hookInput)) { exit 0 }

try { $data = $hookInput | ConvertFrom-Json } catch { exit 0 }

$sid = $data.session_id
if (-not $sid) { exit 0 }

$stateDir = Join-Path $env:USERPROFILE ".claude\state"
if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }

# Determine project name
$project = "unknown"
if ($data.cwd) { $project = Split-Path -Leaf $data.cwd }
elseif ($env:CLAUDE_PROJECT_DIR) { $project = Split-Path -Leaf $env:CLAUDE_PROJECT_DIR }

# Discover host PID (child of WindowsTerminal in process tree)
$hostPid = 0
try {
    $p = [System.Diagnostics.Process]::GetCurrentProcess()
    for ($i = 0; $i -lt 10; $i++) {
        $ppid = (Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)" -ErrorAction SilentlyContinue).ParentProcessId
        if (-not $ppid) { break }
        $pp = [System.Diagnostics.Process]::GetProcessById($ppid)
        if ($pp.ProcessName -eq "WindowsTerminal") { $hostPid = $p.Id; break }
        $p = $pp
    }
} catch {}

# Write timer file
$timerPath = Join-Path $stateDir "cache-timer-$sid.json"
@{
    timestamp  = (Get-Date -Format "o")
    session_id = $sid
    project    = $project
    host_pid   = $hostPid
    stopped    = $true
} | ConvertTo-Json -Compress | Set-Content $timerPath -Force

exit 0
