# PostToolUse / Stop hook for Claude Code (Windows / PowerShell 7)
# Writes a cache timer file so the countdown ticker knows when the last API activity was.
#
# Install: Add to ~/.claude/settings.json under hooks.PostToolUse and hooks.Stop
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

# Determine project name
$project = "unknown"
if ($data.cwd) { $project = Split-Path -Leaf $data.cwd }
elseif ($env:CLAUDE_PROJECT_DIR) { $project = Split-Path -Leaf $env:CLAUDE_PROJECT_DIR }

# Discover host PID (child of WindowsTerminal in process tree)
$hostPid = 0
# Try to read existing PID first (avoid repeated tree walks)
if (Test-Path $timerPath) {
    try {
        $existing = Get-Content $timerPath -Raw | ConvertFrom-Json
        $hostPid = if ($existing.host_pid) { $existing.host_pid } else { 0 }
    } catch {}
}
if ($hostPid -eq 0) {
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
}

# Detect if this is a Stop event
# Claude Code doesn't set HOOK_EVENT in env, but the Stop hook receives
# different JSON structure. Check for stop-specific fields or use the
# hook registration to separate PostToolUse vs Stop.
$isStopped = $false
$stoppedAt = ""

# If registered on Stop event, the hook input won't have tool_name
if (-not $data.tool_name -and -not $data.tool_input) {
    $isStopped = $true
    $stoppedAt = (Get-Date -Format "o")
}

$timer = @{
    timestamp  = (Get-Date -Format "o")
    session_id = $sid
    project    = $project
    host_pid   = $hostPid
    stopped    = $isStopped
}
if ($isStopped) { $timer["stopped_at"] = $stoppedAt }

$timer | ConvertTo-Json -Compress | Set-Content $timerPath -Force
exit 0
