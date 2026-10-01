param([switch]$BridgeOnly)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Wait-OfficeNextPostgres.ps1')
. (Join-Path $PSScriptRoot 'OfficeNext-Process.ps1')
$nextRoot = Join-Path $env:LOCALAPPDATA 'DASLab\ai-office-next'
$runtimePath = Join-Path $nextRoot 'runtime'
$logs = Join-Path $nextRoot 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$nodeExe = (Get-Command node -ErrorAction Stop).Source
# Child-only environment. The shell caller and the original office keep their settings.
foreach ($key in @('OPENAI_API_KEY','ANTHROPIC_API_KEY','CODEX_API_KEY','CODEX_HOME','TUNNEL_TOKEN','DATABASE_URL')) {
    Remove-Item -LiteralPath ('Env:' + $key) -ErrorAction SilentlyContinue
}
Get-ChildItem Env: | Where-Object { $_.Name -like 'DAS_OFFICE_GITHUB_*' } | ForEach-Object {
    Remove-Item -LiteralPath ('Env:' + $_.Name)
}
$env:PAPERCLIP_CODEX_AUTH_CACHE = '0'
$env:PAPERCLIP_NO_BROWSER = 'true'
$env:DO_NOT_TRACK = '1'
function Start-OwnedProcess($Label, $Port, $Arguments, $WorkingPath) {
    # The first measured Windows server restart needed about 160 seconds.
    $readyTimeout = if ($Label -eq 'paperclip') { 240 } else { 30 }
    $ownerId = Get-OfficeNextOwnedListener -Label $Label -Port $Port -NodePath $nodeExe -Arguments $Arguments
    if ($ownerId -gt 0) {
        $readyId = Wait-OfficeNextService -Label $Label -Port $Port -NodePath $nodeExe -Arguments $Arguments -ExpectedProcessId $ownerId -TimeoutSeconds $readyTimeout
        Write-Output "$Label ready (verified PID $readyId, loopback port $Port); left unchanged."
        return
    }
    $pidPath = Join-Path $nextRoot "$Label-process.json"
    if (Test-Path -LiteralPath $pidPath) {
        $prior = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
        $priorId = [int]$prior.pid
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$priorId" -ErrorAction SilentlyContinue
        if (Test-OfficeNextOwnedProcess -Process $proc -NodePath $nodeExe -Arguments $Arguments) {
            $readyId = Wait-OfficeNextService -Label $Label -Port $Port -NodePath $nodeExe -Arguments $Arguments -ExpectedProcessId $priorId -TimeoutSeconds $readyTimeout
            Write-Output "$Label ready (verified PID $readyId, loopback port $Port); existing startup reused."
            return
        }
    }
    $child = Start-Process -FilePath $nodeExe -ArgumentList (ConvertTo-OfficeNextArgumentString -Arguments $Arguments) -WorkingDirectory $WorkingPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "$Label.run.stdout.log") -RedirectStandardError (Join-Path $logs "$Label.run.stderr.log") -PassThru
    @{pid=$child.Id;port=$Port;startedAt=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $pidPath
    $readyId = Wait-OfficeNextService -Label $Label -Port $Port -NodePath $nodeExe -Arguments $Arguments -ExpectedProcessId $child.Id -TimeoutSeconds $readyTimeout
    Write-Output "$Label ready (verified PID $readyId, loopback port $Port)."
}
if (-not $BridgeOnly) {
    $configPath = Join-Path $nextRoot 'state\instances\default\config.json'
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if ($config.database.mode -eq 'postgres') {
        $dbListener = Get-NetTCPConnection -LocalPort 54329 -State Listen -ErrorAction SilentlyContinue
        $dbData = Join-Path $nextRoot 'state\instances\default\db'
        $dbExe = Join-Path $nextRoot 'postgres-native\bin\postgres.exe'
        if ($dbListener) {
            $dbProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($dbListener[0].OwningProcess)"
            if (-not $dbProcess.CommandLine.Contains($dbData)) { throw 'Port 54329 belongs to another database. Left unchanged.' }
        } else {
            if (-not (Test-Path -LiteralPath $dbExe)) { throw 'Prepare the isolated short-path PostgreSQL runtime first.' }
            $dbProcess = Start-Process -FilePath $dbExe -ArgumentList @('-D', $dbData, '-h', '127.0.0.1', '-p', '54329') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'postgres.stdout.log') -RedirectStandardError (Join-Path $logs 'postgres.stderr.log') -PassThru
            @{pid=$dbProcess.Id;port=54329;startedAt=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $nextRoot 'postgres-process.json')
        }
        Wait-OfficeNextPostgres -DbData $dbData -NodePath $nodeExe -ConfigPath $configPath -RuntimePath $runtimePath
    }
    Start-OwnedProcess 'paperclip' 3101 @((Join-Path $runtimePath 'node_modules\paperclipai\dist\index.js'),'run','--data-dir',(Join-Path $nextRoot 'state'),'--no-repair') $runtimePath
}
if (Test-Path -LiteralPath (Join-Path $nextRoot 'office.json')) {
    Start-OwnedProcess 'office-next' 8790 @((Join-Path $PSScriptRoot 'bridge.mjs'),(Join-Path $nextRoot 'office.json')) $PSScriptRoot
}
