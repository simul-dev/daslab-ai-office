#requires -Version 7.2
<# Foreground launcher. Stop it to stop its children. -StartTunnel is explicitly opt-in.
   For an authorized background launch, use Start-Process pwsh -WindowStyle Hidden.
   Requires Windows, PowerShell 7, a configured auth file, and the project's Python environment. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PythonPath,
    [string]$NodePath,
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$CredentialPath = (Join-Path $env:LOCALAPPDATA 'DASLab\ai-office\credentials.clixml'),
    [switch]$StartTunnel,
    [switch]$OfficeNext,
    [string]$CloudflaredPath = (Join-Path $env:LOCALAPPDATA 'DASLab\tools\cloudflared.exe'),
    [ValidateRange(1, 65535)][int]$Port = 8772,
    [ValidateRange(1, 65535)][int]$PublicPort = 8774,
    [ValidateRange(2, 120)][int]$ReadyTimeoutSeconds = 30
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'Windows DPAPI is required.' }
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$nodeDirectory = $null
if ($PSBoundParameters.ContainsKey('NodePath')) {
    if ([string]::IsNullOrWhiteSpace($NodePath)) { throw 'NodePath must name an existing Node.js executable.' }
    $nodeFile = Get-Item -LiteralPath (Resolve-Path -LiteralPath $NodePath).Path
    if ($nodeFile -isnot [IO.FileInfo] -or $nodeFile.Name -ine 'node.exe' -or
        $nodeFile.VersionInfo.OriginalFilename -ine 'node.exe') {
        throw 'NodePath must name a Windows Node.js executable.'
    }
    $nodeDirectory = $nodeFile.DirectoryName
}
$authPath = Join-Path $ProjectRoot 'config\auth.ai-office.local.json'
$auth = Get-Content -LiteralPath $authPath -Raw | ConvertFrom-Json
$origin = [uri]$auth.public_origin
if ($auth.mode -notin @('github', 'pairing') -or $origin.Scheme -ne 'https' -or $Port -eq $PublicPort) {
    throw 'An authenticated HTTPS configuration and distinct local/public ports are required.'
}
$idEnv = $null
$secretEnv = $null
if ($auth.mode -eq 'github') {
    $idEnv = if ($auth.client_id_env) { $auth.client_id_env } else { 'DAS_OFFICE_GITHUB_CLIENT_ID' }
    $secretEnv = if ($auth.client_secret_env) { $auth.client_secret_env } else { 'DAS_OFFICE_GITHUB_CLIENT_SECRET' }
    if ($idEnv -notmatch '^DAS_OFFICE_[A-Z0-9_]+$' -or $secretEnv -notmatch '^DAS_OFFICE_[A-Z0-9_]+$' -or $idEnv -eq $secretEnv) {
        throw 'Use distinct DAS_OFFICE_* credential environment variable names.'
    }
}
$credentials = $null
$server = $null
$tunnel = $null
$client = $null

function Start-PrivateChild([string]$Executable, [string[]]$Arguments, [hashtable]$Secrets, [bool]$Quiet, [string]$RuntimeDirectory = '') {
    $info = [Diagnostics.ProcessStartInfo]::new($Executable)
    $info.UseShellExecute = $false
    $info.WorkingDirectory = $ProjectRoot
    $info.CreateNoWindow = $true
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    # Never forward another child's credentials, including inherited shell values.
    foreach ($name in @($info.Environment.Keys)) {
        if ($name -like 'DAS_OFFICE_*' -or $name -like 'TUNNEL_*') { [void]$info.Environment.Remove($name) }
    }
    if ($RuntimeDirectory) {
        $info.Environment['PATH'] = $RuntimeDirectory + [IO.Path]::PathSeparator + $info.Environment['PATH']
    }
    $info.RedirectStandardOutput = $Quiet
    $info.RedirectStandardError = $Quiet
    $child = [Diagnostics.Process]::new()
    $started = $false
    try {
        foreach ($name in $Secrets.Keys) {
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secrets[$name])
            try { $info.Environment[$name] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        }
        $child.StartInfo = $info
        $started = $child.Start()
        if (-not $started) { throw 'Unable to start a child process.' }
        if ($Quiet) { $child.BeginOutputReadLine(); $child.BeginErrorReadLine() }
        return $child
    } catch {
        if ($started -and -not $child.HasExited) { $child.Kill($true); $child.WaitForExit() }
        $child.Dispose()
        throw
    } finally {
        foreach ($name in $Secrets.Keys) { [void]$info.Environment.Remove($name) }
    }
}

try {
    $required = @()
    if ($auth.mode -eq 'github') { $required += @('GitHubClientId', 'GitHubClientSecret') }
    if ($StartTunnel) { $required += 'TunnelToken'; $CloudflaredPath = (Resolve-Path -LiteralPath $CloudflaredPath).Path }
    if ($required.Count) {
        $credentials = Import-Clixml -LiteralPath $CredentialPath
        if ($credentials -isnot [hashtable] -or $credentials.Version -ne 1) { throw 'Invalid credential store.' }
        foreach ($field in $required) {
            if ($credentials[$field] -isnot [Security.SecureString] -or $credentials[$field].Length -lt 1) {
                throw "Save $field with Save-DasOfficeCredentials.ps1 first."
            }
        }
    }
    $listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    if (@($listeners | Where-Object { $_.Port -in @($Port, $PublicPort) }).Count) {
        throw 'A requested port is already in use. The existing server was left untouched.'
    }
    $serverSecrets = @{}
    if ($auth.mode -eq 'github') {
        $serverSecrets[$idEnv] = $credentials.GitHubClientId
        $serverSecrets[$secretEnv] = $credentials.GitHubClientSecret
    }
    $serverArguments = @(
        (Join-Path $ProjectRoot 'server.py'), '--port', "$Port", '--public-port', "$PublicPort",
        '--auth-config', $authPath, '--data-dir', (Join-Path $ProjectRoot 'data')
    )
    if ($OfficeNext) { $serverArguments += '--office-next' }
    $server = Start-PrivateChild $PythonPath $serverArguments $serverSecrets $false $nodeDirectory
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.UseCookies = $false
    $handler.AllowAutoRedirect = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(2)
    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    $ready = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($server.HasExited) { throw 'The server exited before its authenticated listener was ready.' }
        $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, "http://127.0.0.1:$PublicPort/api/auth")
        $request.Headers.Host = $origin.Authority
        $response = $null
        try {
            $response = $client.Send($request)
            $status = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
            $ready = ($response.StatusCode -eq 200 -and $status.mode -ceq $auth.mode -and
                $status.required -is [bool] -and $status.required -and
                $status.authenticated -is [bool] -and -not $status.authenticated -and $null -eq $status.user)
        } catch { $ready = $false }
        finally { if ($response) { $response.Dispose() }; $request.Dispose() }
        if ($ready) { break }
        Start-Sleep -Milliseconds 200
    }
    if (-not $ready -or $server.HasExited) { throw 'Authentication readiness failed; no tunnel was started.' }
    $privatePaths = @('/api/org')
    if ($OfficeNext) { $privatePaths += '/api/office/snapshot' }
    foreach ($privatePath in $privatePaths) {
        $privateRequest = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, "http://127.0.0.1:$PublicPort$privatePath")
        $privateRequest.Headers.Host = $origin.Authority
        try {
            $privateResponse = $client.Send($privateRequest)
            try {
                if ([int]$privateResponse.StatusCode -ne 401) { throw 'Anonymous private API is reachable; no tunnel was started.' }
            } finally { $privateResponse.Dispose() }
        } finally { $privateRequest.Dispose() }
    }
    if ($StartTunnel) {
        # Token-file is supported, but the process environment avoids even a temporary plaintext file.
        # Drain/discard tunnel output so diagnostics cannot print a token or request headers.
        $tunnel = Start-PrivateChild $CloudflaredPath @(
            'tunnel', '--no-autoupdate', '--loglevel', 'error', '--metrics', '127.0.0.1:0', 'run'
        ) @{ TUNNEL_TOKEN = $credentials.TunnelToken } $true
    }
    Write-Host "Authenticated listener ready on 127.0.0.1:$PublicPort. Stop this launcher to stop its processes."
    while (-not $server.HasExited) {
        if ($tunnel -and $tunnel.HasExited) { throw 'The tunnel process exited; the launcher is stopping its server.' }
        Start-Sleep -Milliseconds 200
    }
    if ($server.ExitCode -ne 0) { throw 'The server process exited unsuccessfully.' }
} finally {
    foreach ($child in @($tunnel, $server)) {
        if ($null -ne $child) {
            try { if (-not $child.HasExited) { $child.Kill($true); $child.WaitForExit() } }
            finally { $child.Dispose() }
        }
    }
    if ($client) { $client.Dispose() }
    if ($credentials -is [hashtable]) {
        foreach ($value in $credentials.Values) { if ($value -is [Security.SecureString]) { $value.Dispose() } }
    }
}
