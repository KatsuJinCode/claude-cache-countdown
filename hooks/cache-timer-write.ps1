# cache-timer-stop.ps1 - Stop hook
# Marks the cache timer file as "stopped" so the external ticker knows
# the cache is now genuinely draining and should show the countdown.
#
# FAILURE POLICY: Nothing silent. Every error logs AND sends a toast notification.
param()

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$stateDir = Join-Path $env:USERPROFILE ".claude\state"
$errorLog = Join-Path $stateDir "cache-timer-errors.log"

function Write-Failure {
    param([string]$Where, [string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [STOP] [$Where] $Message"
    try { Add-Content $errorLog $line } catch {}
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        $template = @"
<toast>
  <visual><binding template='ToastGeneric'>
    <text>Cache Timer Hook FAILED</text>
    <text>[$Where] $Message</text>
  </binding></visual>
</toast>
"@
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml($template)
        $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Claude Cache Timer").Show($toast)
    } catch {
        try {
            Import-Module BurntToast -ErrorAction Stop
            New-BurntToastNotification -Text "Cache Timer FAILED", "[$Where] $Message" -ErrorAction Stop
        } catch {
            [Console]::Beep(1000, 500)
            [Console]::Error.WriteLine("CACHE-TIMER-STOP FAILED: [$Where] $Message")
        }
    }
}

function Write-Debug {
    param([string]$Message)
    $debugLog = Join-Path $stateDir "cache-timer-debug.log"
    try { Add-Content $debugLog "$(Get-Date -Format 'HH:mm:ss') [STOP] $Message" } catch {}
}

# --- Read hook input ---
try {
    $hookInput = [Console]::In.ReadToEnd()
} catch {
    Write-Failure "stdin" "Failed to read hook input: $_"
    exit 1
}
if ([string]::IsNullOrWhiteSpace($hookInput)) { exit 0 }

try {
    $data = $hookInput | ConvertFrom-Json
} catch {
    Write-Failure "parse-input" "Failed to parse hook JSON: $_"
    exit 1
}

$sid = $data.session_id
if (-not $sid) {
    Write-Failure "no-sid" "Hook input has no session_id. Input: $hookInput"
    exit 1
}

if (-not (Test-Path $stateDir)) {
    try {
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    } catch {
        Write-Failure "mkdir" "Failed to create state dir: $_"
        exit 1
    }
}

$cacheTimerPath = Join-Path $stateDir "cache-timer-$sid.json"

# --- Read existing timer file ---
$timerData = @{}
if (Test-Path $cacheTimerPath) {
    try {
        $existing = Get-Content $cacheTimerPath -Raw | ConvertFrom-Json
        $existing.PSObject.Properties | ForEach-Object { $timerData[$_.Name] = $_.Value }
    } catch {
        Write-Failure "read-timer" "Failed to read existing timer file $cacheTimerPath : $_"
        $timerData = @{}
    }
}

# --- Populate project and cwd ---
if (-not $timerData["project"]) {
    if ($data.cwd) {
        $timerData["project"] = Split-Path -Leaf $data.cwd
    } elseif ($env:CLAUDE_PROJECT_DIR) {
        $timerData["project"] = Split-Path -Leaf $env:CLAUDE_PROJECT_DIR
    } else {
        $timerData["project"] = "unknown"
    }
}
if ($data.cwd) {
    $timerData["cwd"] = $data.cwd
}

# --- Mark as stopped ---
$timerData["stopped"] = $true
$timerData["timestamp"] = (Get-Date -Format "o")
$timerData["session_id"] = $sid

try {
    $timerData | ConvertTo-Json -Compress | Set-Content $cacheTimerPath -Force
} catch {
    Write-Failure "write-timer" "Failed to write timer file $cacheTimerPath : $_"
    exit 1
}

# --- PID discovery: verify cached PID is alive, re-walk if not ---
$cachedPid = $timerData["host_pid"]
$pidAlive = $false
if ($cachedPid -and $cachedPid -ne 0) {
    try {
        [System.Diagnostics.Process]::GetProcessById($cachedPid) | Out-Null
        $pidAlive = $true
    } catch {
        $pidAlive = $false
    }
}

$needsWalk = (-not $cachedPid -or $cachedPid -eq 0 -or -not $pidAlive)

if ($needsWalk) {
    $walkResult = 0
    $walkTrace = @()
    try {
        $p = [System.Diagnostics.Process]::GetCurrentProcess()
        $walkTrace += "self=$($p.Id)($($p.ProcessName))"
        for ($i = 0; $i -lt 10; $i++) {
            $wmiResult = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)" -ErrorAction Stop
            $ppid = $wmiResult.ParentProcessId
            if (-not $ppid -or $ppid -eq 0) {
                $walkTrace += "-> ppid=null(end)"
                break
            }
            try {
                $pp = [System.Diagnostics.Process]::GetProcessById($ppid)
            } catch {
                $walkTrace += "-> $ppid(DEAD)"
                break
            }
            $walkTrace += "-> $($pp.Id)($($pp.ProcessName))"
            if ($pp.ProcessName -eq "WindowsTerminal") {
                $walkResult = $p.Id
                $walkTrace += "FOUND=$($p.Id)"
                break
            }
            $p = $pp
        }
    } catch {
        $walkTrace += "ERROR: $_"
        Write-Failure "pid-walk" "PID walk failed for sid=$sid. Trace: $($walkTrace -join ' '). Error: $_"
    }

    Write-Debug "sid=$sid cachedPid=$cachedPid pidAlive=$pidAlive walk=[$($walkTrace -join ' ')] result=$walkResult"

    if ($walkResult -ne 0) {
        $timerData["host_pid"] = $walkResult
        try {
            $timerData | ConvertTo-Json -Compress | Set-Content $cacheTimerPath -Force
        } catch {
            Write-Failure "write-pid" "Found PID $walkResult but failed to write: $_"
        }
    } else {
        Write-Debug "sid=$sid WARNING: PID walk found nothing. Tab title will not update."
    }
} else {
    Write-Debug "sid=$sid cachedPid=$cachedPid ALIVE(skip walk)"
}

exit 0
