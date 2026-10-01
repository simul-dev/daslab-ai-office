function Get-OfficeNextProcessArguments {
    param([string]$CommandLine)
    if (-not $CommandLine) { return @() }
    if (-not ('OfficeNext.NativeArguments' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace OfficeNext {
    public static class NativeArguments {
        [DllImport("shell32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern IntPtr CommandLineToArgvW(string commandLine, out int count);
        [DllImport("kernel32.dll")]
        private static extern IntPtr LocalFree(IntPtr memory);
        public static string[] Parse(string commandLine) {
            int count;
            IntPtr memory = CommandLineToArgvW(commandLine, out count);
            if (memory == IntPtr.Zero) throw new InvalidOperationException("Cannot inspect process arguments.");
            try {
                var result = new string[count];
                for (int i = 0; i < count; i++)
                    result[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(memory, i * IntPtr.Size));
                return result;
            } finally { LocalFree(memory); }
        }
    }
}
'@
    }
    return [OfficeNext.NativeArguments]::Parse($CommandLine)
}

function Test-OfficeNextOwnedProcess {
    param($Process, [string]$NodePath, [string[]]$Arguments)
    if (-not $Process -or -not $Process.ExecutablePath -or -not $Process.CommandLine) { return $false }
    if (-not [string]::Equals([IO.Path]::GetFullPath($Process.ExecutablePath), [IO.Path]::GetFullPath($NodePath), [StringComparison]::OrdinalIgnoreCase)) { return $false }
    $actual = @(Get-OfficeNextProcessArguments -CommandLine $Process.CommandLine)
    if ($actual.Count -ne $Arguments.Count + 1) { return $false }
    if (-not [string]::Equals([IO.Path]::GetFullPath($actual[0]), [IO.Path]::GetFullPath($NodePath), [StringComparison]::OrdinalIgnoreCase)) { return $false }
    for ($index = 0; $index -lt $Arguments.Count; $index++) {
        if (-not [string]::Equals($actual[$index + 1], $Arguments[$index], [StringComparison]::OrdinalIgnoreCase)) { return $false }
    }
    return $true
}

function Get-OfficeNextOwnedListener {
    param([string]$Label, [int]$Port, [string]$NodePath, [string[]]$Arguments, [int]$ExpectedProcessId = 0)
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    $ownerId = 0
    foreach ($listener in $listeners) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
        if ($listener.LocalAddress -ne '127.0.0.1' -or
            -not (Test-OfficeNextOwnedProcess -Process $owner -NodePath $NodePath -Arguments $Arguments) -or
            ($ExpectedProcessId -gt 0 -and $listener.OwningProcess -ne $ExpectedProcessId) -or
            ($ownerId -gt 0 -and $listener.OwningProcess -ne $ownerId)) {
            throw "$Label port $Port is occupied by an unverified process. Nothing was stopped."
        }
        $ownerId = [int]$listener.OwningProcess
    }
    return $ownerId
}

function Test-OfficeNextJsonReady {
    param([string]$Label, [int]$Port)
    $endpoint = if ($Label -eq 'paperclip') { '/api/companies' } elseif ($Label -eq 'office-next') { '/api/office/snapshot' } else { throw 'Unknown readiness endpoint.' }
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port$endpoint" -TimeoutSec 2 -MaximumRedirection 0
        if ($response.StatusCode -ne 200 -or $response.Headers['Content-Type'] -notmatch 'application/json') { return $false }
        $json = ConvertFrom-Json -InputObject $response.Content -NoEnumerate
        if ($Label -eq 'paperclip') {
            if ($json -isnot [array]) { return $false }
            foreach ($company in $json) {
                if ($company.id -isnot [string] -or $company.name -isnot [string]) { return $false }
            }
            return $true
        }
        return $json.mode -eq 'pilot' -and $json.company.id -is [string] -and $json.agents -is [array] -and $json.issues -is [array]
    } catch { return $false }
}

function Wait-OfficeNextService {
    param([string]$Label, [int]$Port, [string]$NodePath, [string[]]$Arguments,
        [int]$ExpectedProcessId = 0, [ValidateRange(1,300)][int]$TimeoutSeconds = 30)
    $elapsed = [Diagnostics.Stopwatch]::StartNew()
    $nextProgressSeconds = 30
    while ($elapsed.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        $ownerId = Get-OfficeNextOwnedListener -Label $Label -Port $Port -NodePath $NodePath -Arguments $Arguments -ExpectedProcessId $ExpectedProcessId
        if ($ownerId -gt 0 -and (Test-OfficeNextJsonReady -Label $Label -Port $Port)) { return $ownerId }
        if ($ExpectedProcessId -gt 0) {
            $expected = Get-CimInstance Win32_Process -Filter "ProcessId=$ExpectedProcessId" -ErrorAction SilentlyContinue
            if (-not (Test-OfficeNextOwnedProcess -Process $expected -NodePath $NodePath -Arguments $Arguments)) {
                throw "$Label exited or changed identity before becoming ready. Nothing was stopped."
            }
        }
        if ($elapsed.Elapsed.TotalSeconds -ge $nextProgressSeconds) {
            Write-Host "$Label is still starting; waiting for this verified process. No duplicate started."
            $nextProgressSeconds += 30
        }
        Start-Sleep -Milliseconds 200
    }
    throw "$Label did not return its expected JSON response in time. No duplicate was started; inspect its log."
}

function ConvertTo-OfficeNextArgumentString {
    param([string[]]$Arguments)
    return (($Arguments | ForEach-Object {
        if ($_.Contains('"')) { throw 'A launch argument contains an unsupported quote.' }
        '"' + ($_ -replace '(\\+)$', '$1$1') + '"'
    }) -join ' ')
}
