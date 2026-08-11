[CmdletBinding()]
param(
    [int]$ReconnectTimeoutSeconds = 180,
    [int]$StartupWaitSeconds = 20
)

$ErrorActionPreference = "Stop"
$started = Get-Date
$projectPath = "C:\Users\trener-sim\Documents\RobotStudio\Projects\abb_sim"
$sdkPath = "C:\Program Files (x86)\ABB\RobotStudio 2026\Bin-net48\ABB.Robotics.Controllers.PC.dll"
$targetName = "abb_controller"
$controller = $null

function Get-EventSnapshot {
    param([Parameter(Mandatory = $true)]$EventLog)

    $items = @()
    foreach ($categoryId in @($EventLog.GetCategoryIds())) {
        $category = $null
        try {
            $category = $EventLog.GetCategory([int]$categoryId)
            foreach ($message in @($category.GetMessageRange(1000))) {
                $items += [pscustomobject]@{
                    SequenceNumber = [int]$message.SequenceNumber
                    Timestamp      = [datetime]$message.Timestamp
                    Type           = [string]$message.Type
                    Number         = [int]$message.Number
                    CategoryId     = [int]$message.CategoryId
                    Category       = [string]$message.CategoryType
                    Title          = [string]$message.Title
                    Body           = [string]$message.Body
                }
            }
        }
        finally {
            if ($null -ne $category) { $category.Dispose() }
        }
    }
    return @($items)
}

try {
    if (-not (Test-Path -LiteralPath $sdkPath)) {
        throw "PC SDK assembly not found: $sdkPath"
    }

    [Environment]::CurrentDirectory = Split-Path -Parent $sdkPath
    [Reflection.Assembly]::LoadFrom($sdkPath) | Out-Null

    $scanner = New-Object ABB.Robotics.Controllers.Discovery.NetworkScanner
    $scanStarted = Get-Date
    $scanner.Scan()
    $scanSeconds = ((Get-Date) - $scanStarted).TotalSeconds

    $discovered = @($scanner.Controllers)
    $matches = @($discovered | Where-Object {
        $_.IsVirtual -and
        ($_.ControllerName -eq $targetName -or $_.SystemName -eq $targetName -or $_.Name -eq $targetName) -and
        $null -ne $_.BaseDirectory -and
        $_.BaseDirectory.FullName.StartsWith($projectPath, [StringComparison]::OrdinalIgnoreCase)
    })

    if ($matches.Count -ne 1) {
        $summary = @($discovered | ForEach-Object {
            "$($_.ControllerName)|$($_.SystemName)|virtual=$($_.IsVirtual)|base=$($_.BaseDirectory)"
        }) -join "; "
        throw "Fail-closed controller selection: expected exactly one virtual $targetName under $projectPath; matches=$($matches.Count); discovered=$summary"
    }

    $info = $matches[0]
    if (-not $info.IsVirtual) { throw "Selected controller is not virtual" }

    $controller = [ABB.Robotics.Controllers.ControllerFactory]::CreateFrom($info)
    $controller.Logon([ABB.Robotics.Controllers.UserInfo]::DefaultUser)

    $baselineCaptured = Get-Date
    $baselineEvents = @(Get-EventSnapshot -EventLog $controller.EventLog)
    $baselineMaxSequence = if ($baselineEvents.Count) {
        ($baselineEvents | Measure-Object SequenceNumber -Maximum).Maximum
    } else { -1 }

    $restartStarted = Get-Date
    $restartMode = [ABB.Robotics.Controllers.ControllerStartMode]::PStart
    $mastership = $null
    try {
        $mastership = [ABB.Robotics.Controllers.Mastership]::Request($controller)
        $controller.RestartAndWaitForReconnect($restartMode)
    }
    finally {
        if ($null -ne $mastership) { $mastership.Dispose() }
    }
    $reconnectSeconds = ((Get-Date) - $restartStarted).TotalSeconds
    if ($reconnectSeconds -gt $ReconnectTimeoutSeconds) {
        throw "Reconnect exceeded bounded timeout: $reconnectSeconds seconds"
    }

    $startupStarted = Get-Date
    $deadline = $startupStarted.AddSeconds($StartupWaitSeconds)
    do {
        Start-Sleep -Seconds 2
        $taskProbe = @($controller.Rapid.GetTasks())
        $wantedProbe = @($taskProbe | Where-Object { $_.Name -in @("T_ARCI_CTRL", "T_ARCI_DATA") })
    } while ($wantedProbe.Count -ne 2 -and (Get-Date) -lt $deadline)
    $startupSeconds = ((Get-Date) - $startupStarted).TotalSeconds

    $tasks = @($controller.Rapid.GetTasks() | Where-Object { $_.Name -in @("T_ARCI_CTRL", "T_ARCI_DATA") } |
        ForEach-Object {
            [pscustomobject]@{
                Name            = [string]$_.Name
                ExecutionStatus = [string]$_.ExecutionStatus
                ExecutionType   = [string]$_.ExecutionType
            }
        })

    $afterEvents = @(Get-EventSnapshot -EventLog $controller.EventLog)
    $newEvents = @($afterEvents | Where-Object {
        $_.SequenceNumber -gt $baselineMaxSequence -and
        $_.Timestamp -ge $baselineCaptured -and
        $_.Type -match "Warning|Error"
    } | Sort-Object SequenceNumber, Number, Title -Unique | ForEach-Object {
        $combined = "$($_.Title) $($_.Body)"
        $taskContext = if ($combined -match "\b(T_[A-Z0-9_]+)\b") { $Matches[1] } else { $null }
        $programReference = if ($combined -match "(?i)Program ref:\s*([^<\r\n]+)") {
            $Matches[1]
        } else { $null }
        [pscustomobject]@{
            Timestamp        = $_.Timestamp.ToString("o")
            SequenceNumber   = $_.SequenceNumber
            Type             = $_.Type
            Number           = $_.Number
            CategoryId       = $_.CategoryId
            Category         = $_.Category
            TaskContext      = $taskContext
            Title            = $_.Title
            Body             = $_.Body
            ProgramReference = $programReference
        }
    })

    $tcp = @(55000, 55001 | ForEach-Object {
        $port = $_
        $probe = Test-NetConnection -ComputerName 192.168.50.1 -Port $port -WarningAction SilentlyContinue
        [pscustomobject]@{
            Host          = "192.168.50.1"
            Port          = $port
            TcpSucceeded  = [bool]$probe.TcpTestSucceeded
            SourceAddress = [string]$probe.SourceAddress.IPAddress
        }
    })

    [pscustomobject]@{
        Success = $true
        ExitCode = 0
        ScriptPath = $MyInvocation.MyCommand.Path
        Controller = [pscustomobject]@{
            ControllerName = [string]$info.ControllerName
            SystemName     = [string]$info.SystemName
            SystemId       = [string]$info.SystemId
            IPAddress      = [string]$info.IPAddress
            IsVirtual      = [bool]$info.IsVirtual
            BaseDirectory  = [string]$info.BaseDirectory.FullName
            Version        = [string]$info.Version
        }
        ApiCalls = @(
            "NetworkScanner.Scan",
            "ControllerFactory.CreateFrom",
            "Controller.Logon(DefaultUser)",
            "EventLog.GetCategoryIds/GetCategory/GetMessageRange",
            "Mastership.Request(Controller)",
            "Controller.RestartAndWaitForReconnect(PStart)",
            "Rapid.GetTasks"
        )
        TimingsSeconds = [pscustomobject]@{
            Scan      = [math]::Round($scanSeconds, 3)
            Reconnect = [math]::Round($reconnectSeconds, 3)
            Startup   = [math]::Round($startupSeconds, 3)
            Total     = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
        }
        EventBaseline = [pscustomobject]@{
            CapturedAt  = $baselineCaptured.ToString("o")
            Count       = $baselineEvents.Count
            MaxSequence = $baselineMaxSequence
        }
        Tasks = $tasks
        NewWarningErrorEvents = $newEvents
        TcpTests = $tcp
    } | ConvertTo-Json -Depth 8
    exit 0
}
catch {
    $errorMessages = @()
    $currentError = $_.Exception
    while ($null -ne $currentError) {
        $errorMessages += "$($currentError.GetType().FullName): $($currentError.Message)"
        if ($currentError -is [AggregateException]) {
            $errorMessages += @($currentError.Flatten().InnerExceptions | ForEach-Object {
                "$($_.GetType().FullName): $($_.Message)"
            })
        }
        $currentError = $currentError.InnerException
    }
    [pscustomobject]@{
        Success   = $false
        ExitCode  = 1
        ScriptPath = $MyInvocation.MyCommand.Path
        Error     = $errorMessages -join " | "
        ScriptStackTrace = [string]$_.ScriptStackTrace
        TotalSeconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
    } | ConvertTo-Json -Depth 5
    exit 1
}
finally {
    if ($null -ne $controller) {
        try { $controller.Logoff() } catch {}
        try { $controller.Dispose() } catch {}
    }
}
