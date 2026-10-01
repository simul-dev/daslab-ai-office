function Test-OfficeNextPostgresReady {
    param([string]$NodePath, [string]$ConfigPath, [string]$RuntimePath)
    & $NodePath (Join-Path $PSScriptRoot 'postgres-readiness.mjs') $ConfigPath $RuntimePath
    return $LASTEXITCODE -eq 0
}

function Wait-OfficeNextPostgres {
    param(
        [Parameter(Mandatory)][string]$DbData,
        [Parameter(Mandatory)][string]$NodePath,
        [Parameter(Mandatory)][string]$ConfigPath,
        [Parameter(Mandatory)][string]$RuntimePath,
        [ValidateRange(1,60)][int]$TimeoutSeconds = 30
    )
    $elapsed = [Diagnostics.Stopwatch]::StartNew()
    $dataArgument = '(?:^|\s)-D\s+(?:"' + [regex]::Escape($DbData) + '"|' + [regex]::Escape($DbData) + ')(?=\s|$)'
    while ($elapsed.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        $listeners = @(Get-NetTCPConnection -LocalPort 54329 -State Listen -ErrorAction SilentlyContinue)
        if ($listeners.Count -gt 0) {
            foreach ($listener in $listeners) {
                $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
                if ($listener.LocalAddress -ne '127.0.0.1' -or -not $owner -or
                    $owner.Name -ne 'postgres.exe' -or -not $owner.CommandLine -or
                    $owner.CommandLine -notmatch $dataArgument) {
                    throw 'Port 54329 is not the isolated loopback database. Nothing was stopped.'
                }
            }
            if (Test-OfficeNextPostgresReady -NodePath $NodePath -ConfigPath $ConfigPath -RuntimePath $RuntimePath) { return }
        }
        Start-Sleep -Milliseconds 200
    }
    throw 'The isolated PostgreSQL database did not become ready in time. Paperclip was not started; inspect its PostgreSQL log.'
}
