# cache-timer-resume.ps1 - UserPromptSubmit hook
# Clears the stopped state on the cache timer file when the user sends a new prompt.
# This tells the ticker the session is active again (cache is being refreshed).
#
# FAILURE POLICY: Nothing silent. Every error logs AND pops a dialog.
param()

$ErrorActionPreference = "Stop"

$stateDir = Join-Path $env:USERPROFILE ".claude\state"
$errorLog = Join-Path $stateDir "cache-timer-errors.log"

function Write-Failure {
    param([string]$Where, [string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [RESUME] [$Where] $Message"
    try { Add-Content $errorLog $line } catch {}
    # Toast notification - non-blocking, impossible to miss, won't freeze the session
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
        # If toast fails, BurntToast fallback, then console beep + stderr
        try {
            Import-Module BurntToast -ErrorAction Stop
            New-BurntToastNotification -Text "Cache Timer FAILED", "[$Where] $Message" -ErrorAction Stop
        } catch {
            [Console]::Beep(1000, 500)
            [Console]::Error.WriteLine("CACHE-TIMER-RESUME FAILED: [$Where] $Message")
        }
    }
}

function Write-Debug {
    param([string]$Message)
    $debugLog = Join-Path $stateDir "cache-timer-debug.log"
    try { Add-Content $debugLog "$(Get-Date -Format 'HH:mm:ss') [RESUME] $Message" } catch {}
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

$timerPath = Join-Path $stateDir "cache-timer-$sid.json"

# --- Read existing timer file ---
$ht = @{}
if (Test-Path $timerPath) {
    try {
        $timer = Get-Content $timerPath -Raw | ConvertFrom-Json
        $timer.PSObject.Properties | ForEach-Object { $ht[$_.Name] = $_.Value }
    } catch {
        Write-Failure "read-timer" "Failed to read existing timer file $timerPath : $_"
        # Continue with empty hash - we can still write a fresh one
        $ht = @{}
    }
}

# --- Populate project ---
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

# --- Write timer immediately (before PID walk which can be slow) ---
$ht["stopped"] = $false
$ht["timestamp"] = (Get-Date -Format "o")
$ht["session_id"] = $sid
$ht.Remove("stopped_at")

try {
    $ht | ConvertTo-Json -Compress | Set-Content $timerPath -Force
} catch {
    Write-Failure "write-timer" "Failed to write timer file $timerPath : $_"
    exit 1
}

# --- PID discovery: verify cached PID is alive, re-walk if not ---
$cachedPid = $ht["host_pid"]
$pidAlive = $false
if ($cachedPid -and $cachedPid -ne 0) {
    try {
        [System.Diagnostics.Process]::GetProcessById($cachedPid) | Out-Null
        $pidAlive = $true
    } catch {
        # PID is dead, will re-walk
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
        $ht["host_pid"] = $walkResult
        try {
            $ht | ConvertTo-Json -Compress | Set-Content $timerPath -Force
        } catch {
            Write-Failure "write-pid" "Found PID $walkResult but failed to write: $_"
        }
    } else {
        Write-Debug "sid=$sid WARNING: PID walk found nothing. Tab title will not update."
    }
} else {
    Write-Debug "sid=$sid cachedPid=$cachedPid ALIVE(skip walk)"
}

# --- Clean up stale timer files sharing same host_pid ---
$myPid = $ht["host_pid"]
if ($myPid -and $myPid -ne 0) {
    try {
        Get-ChildItem (Join-Path $stateDir "cache-timer-*.json") -ErrorAction Stop | Where-Object {
            $_.Name -ne "cache-timer-$sid.json"
        } | ForEach-Object {
            try {
                $other = Get-Content $_.FullName -Raw -ErrorAction Stop | ConvertFrom-Json
                if ($other.host_pid -eq $myPid) {
                    Remove-Item $_.FullName -Force -ErrorAction Stop
                    Write-Debug "sid=$sid cleaned stale timer $($_.Name) (shared pid=$myPid)"
                }
            } catch {
                Write-Failure "cleanup-read" "Failed reading/cleaning $($_.FullName): $_"
            }
        }
    } catch {
        Write-Failure "cleanup-list" "Failed listing timer files for cleanup: $_"
    }
}

exit 0
