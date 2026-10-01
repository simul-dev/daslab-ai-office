#requires -Version 7.2
<# Offline Windows regression check: random dummy credentials, temporary fake servers, no real tunnel. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PythonPath,
    [string]$NodePath = (Get-Command node -CommandType Application -ErrorAction Stop).Source
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'Windows DPAPI is required.' }
$scriptsDirectory = Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts'
$NodePath = (Resolve-Path -LiteralPath $NodePath).Path
$originalPath = [Environment]::GetEnvironmentVariable('PATH', 'Process')
$plainPath = Join-Path $env:SystemRoot 'System32'
function Assert-Check($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Get-DummyHash([string]$Value) {
    return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($Value))).ToLowerInvariant()
}
function Get-FreePort {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return $listener.LocalEndpoint.Port } finally { $listener.Stop() }
}
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('DAS Office test ' + [guid]::NewGuid().ToString('N'))
$project = Join-Path $scratch 'project with spaces'
$store = Join-Path $scratch 'private\credentials.clixml'
$savedEnvironment = @{}
$fields = @('DAS_OFFICE_GITHUB_CLIENT_ID', 'DAS_OFFICE_GITHUB_CLIENT_SECRET', 'TUNNEL_TOKEN')
$dummy = @{}
foreach ($field in $fields) {
    $savedEnvironment[$field] = [Environment]::GetEnvironmentVariable($field, 'Process')
    $dummy[$field] = [guid]::NewGuid().ToString('N')
}
$answers = [Collections.Generic.Queue[Security.SecureString]]::new()
function Read-Host {
    param([string]$Prompt, [switch]$AsSecureString)
    Assert-Check $AsSecureString 'Credential input must be masked.'
    return $answers.Dequeue()
}
try {
    New-Item -ItemType Directory -Path (Join-Path $project 'config') -Force | Out-Null
    foreach ($field in $fields[0..1]) { $answers.Enqueue((ConvertTo-SecureString $dummy[$field] -AsPlainText -Force)) }
    & (Join-Path $scriptsDirectory 'Save-DasOfficeCredentials.ps1') -Kind GitHub -CredentialPath $store | Out-Null
    $answers.Enqueue((ConvertTo-SecureString $dummy.TUNNEL_TOKEN -AsPlainText -Force))
    & (Join-Path $scriptsDirectory 'Save-DasOfficeCredentials.ps1') -Kind Tunnel -CredentialPath $store | Out-Null
    $answers.Enqueue((ConvertTo-SecureString ('cloudflared.exe service install ' + $dummy.TUNNEL_TOKEN) -AsPlainText -Force))
    & (Join-Path $scriptsDirectory 'Save-DasOfficeCredentials.ps1') -Kind Tunnel -CredentialPath $store | Out-Null
    $beforeInvalid = (Get-FileHash -LiteralPath $store).Hash
    $invalidTunnelInputs = @(
        (' ' + $dummy.TUNNEL_TOKEN), ($dummy.TUNNEL_TOKEN + ' '), ($dummy.TUNNEL_TOKEN + "`n"),
        ('cloudflared.exe  service install ' + $dummy.TUNNEL_TOKEN),
        ('cloudflared.exe service install ' + $dummy.TUNNEL_TOKEN + ' --extra'),
        ('cloudflared.exe service install ' + $dummy.TUNNEL_TOKEN + '; Write-Output rejected'),
        ('cloudflared.exe service install ' + $dummy.TUNNEL_TOKEN + ' && echo rejected'),
        'cloudflared.exe service install ****', 'cloudflared.exe service install "quoted"'
    )
    Assert-Check ($invalidTunnelInputs.Count -eq 9) 'All tunnel parser boundaries must be tested.'
    foreach ($invalid in $invalidTunnelInputs) {
        $answers.Enqueue((ConvertTo-SecureString $invalid -AsPlainText -Force))
        $failed = $false
        try { & (Join-Path $scriptsDirectory 'Save-DasOfficeCredentials.ps1') -Kind Tunnel -CredentialPath $store | Out-Null }
        catch { $failed = $true }
        Assert-Check $failed 'Malformed tunnel input should fail.'
        Assert-Check ((Get-FileHash -LiteralPath $store).Hash -ceq $beforeInvalid) 'Malformed tunnel input changed the existing store.'
    }
    foreach ($invalid in @('', ' ', "line`nbreak")) {
        $answers.Enqueue((ConvertTo-SecureString $dummy.DAS_OFFICE_GITHUB_CLIENT_ID -AsPlainText -Force))
        $invalidSecure = [Security.SecureString]::new()
        foreach ($character in $invalid.ToCharArray()) { $invalidSecure.AppendChar($character) }
        $answers.Enqueue($invalidSecure)
        $failed = $false
        try { & (Join-Path $scriptsDirectory 'Save-DasOfficeCredentials.ps1') -Kind GitHub -CredentialPath $store | Out-Null }
        catch { $failed = $true }
        Assert-Check $failed 'Empty/whitespace input should fail.'
        Assert-Check ((Get-FileHash -LiteralPath $store).Hash -ceq $beforeInvalid) 'Invalid input changed the existing store.'
    }
    $encrypted = Get-Content -LiteralPath $store -Raw
    foreach ($value in $dummy.Values) { Assert-Check (-not $encrypted.Contains($value)) 'Plaintext found in the credential store.' }
    $restored = Import-Clixml -LiteralPath $store
    $mapping = @{ GitHubClientId = $fields[0]; GitHubClientSecret = $fields[1]; TunnelToken = $fields[2] }
    foreach ($key in $mapping.Keys) {
        Assert-Check ($restored[$key] -is [Security.SecureString]) 'A credential was not DPAPI-restored.'
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($restored[$key])
        try { Assert-Check ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) -ceq $dummy[$mapping[$key]]) 'Credential preservation failed.' }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer); $restored[$key].Dispose() }
    }
    $allowedSids = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18')
    foreach ($ace in (Get-Acl -LiteralPath $store).Access) {
        Assert-Check ($ace.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -in $allowedSids) 'Credential file access is too broad.'
    }
    @{ mode = 'github'; public_origin = 'https://office.example.test'; allowed_github_ids = @(1) } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $project 'config\auth.ai-office.local.json')
    @'
import hashlib, http.server, json, os, pathlib, shutil, subprocess, sys, time
root = pathlib.Path(__file__).resolve().parent
scenario = json.loads((root / 'scenario.json').read_text())
node = shutil.which('node')
node_version = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=10) if node else None
names = ['DAS_OFFICE_GITHUB_CLIENT_ID', 'DAS_OFFICE_GITHUB_CLIENT_SECRET']
checks = {
    'credentials_match': (all(hashlib.sha256(os.environ.get(n, '').encode()).hexdigest() == scenario['hashes'][n] for n in names)
                          if scenario['auth_mode'] == 'github' else all(n not in os.environ for n in names)),
    'no_tunnel_secret': 'TUNNEL_TOKEN' not in os.environ,
    'node_discovered': node is not None and pathlib.Path(node).samefile(scenario['node']),
    'node_executable': node_version is not None and node_version.returncode == 0 and node_version.stdout.strip().startswith('v'),
    'server_path_only': os.environ['PATH'] == str(pathlib.Path(scenario['node']).parent) + os.pathsep + scenario['parent_path'],
    'arguments_exact': sys.argv[1:] == ['--port', str(scenario['port']), '--public-port', str(scenario['public_port']), '--auth-config', str(root / 'config' / 'auth.ai-office.local.json'), '--data-dir', str(root / 'data')],
    'pid': os.getpid(),
}
(root / 'server-result.json').write_text(json.dumps(checks))
if scenario['mode'] == 'exit': sys.exit(7)
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        assert self.headers['Host'] == 'office.example.test'
        if self.path == '/api/org':
            self.send_response(200 if scenario['mode'] == 'open_private' else 401)
            self.send_header('Content-Length', '0'); self.end_headers(); return
        assert self.path == '/api/auth'
        status = {'mode': scenario['auth_mode'], 'required': True, 'authenticated': False, 'user': None}
        if scenario['mode'] == 'invalid': status['mode'] = 'local'
        if scenario['mode'] == 'string_bool': status['required'] = 'true'
        payload = json.dumps(status).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
with http.server.HTTPServer(('127.0.0.1', scenario['public_port']), Handler) as server:
    server.timeout = 0.1
    deadline = time.monotonic() + (1.5 if scenario['mode'] == 'valid' else 8)
    while time.monotonic() < deadline: server.handle_request()
'@ | Set-Content -LiteralPath (Join-Path $project 'server.py')
    # Python stands in for cloudflared; its first fixed argument names this local test script.
    @'
import hashlib, json, os, pathlib, sys, time
root = pathlib.Path(__file__).resolve().parent
scenario = json.loads((root / 'scenario.json').read_text())
checks = {
    'token_match': hashlib.sha256(os.environ.get('TUNNEL_TOKEN', '').encode()).hexdigest() == scenario['hashes']['TUNNEL_TOKEN'],
    'no_oauth_secret': not any(n.startswith('DAS_OFFICE_') for n in os.environ),
    'tunnel_path_unchanged': os.environ['PATH'] == scenario['parent_path'],
    'arguments_exact': sys.argv[1:] == ['--no-autoupdate', '--loglevel', 'error', '--metrics', '127.0.0.1:0', 'run'],
    'pid': os.getpid(),
}
(root / 'tunnel-result.json').write_text(json.dumps(checks))
print(os.environ['TUNNEL_TOKEN'], flush=True)
print(os.environ['TUNNEL_TOKEN'], file=sys.stderr, flush=True)
if scenario.get('tunnel_fail'): sys.exit(7)
time.sleep(15)
'@ | Set-Content -LiteralPath (Join-Path $project 'tunnel')
    foreach ($field in $fields) { [Environment]::SetEnvironmentVariable($field, 'inherited-test-sentinel', 'Process') }
    # Reproduce a normal PowerShell session without the Codex runtime directory.
    [Environment]::SetEnvironmentVariable('PATH', $plainPath, 'Process')
    Assert-Check (-not (Get-Command node -CommandType Application -ErrorAction SilentlyContinue)) 'The parent fixture must not discover Node.'
    $hashes = @{}
    foreach ($field in $fields) { $hashes[$field] = Get-DummyHash $dummy[$field] }
    foreach ($case in @('valid', 'invalid', 'string_bool', 'exit', 'tunnel_fail', 'occupied', 'open_private', 'pairing')) {
        foreach ($file in @('server-result.json', 'tunnel-result.json')) {
            $path = Join-Path $project $file
            if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
        }
        $port = Get-FreePort
        do { $publicPort = Get-FreePort } while ($publicPort -eq $port)
        $authMode = if ($case -eq 'pairing') { 'pairing' } else { 'github' }
        @{ mode = $authMode; public_origin = 'https://office.example.test'; allowed_github_ids = @(1) } |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $project 'config\auth.ai-office.local.json')
        $scenario = @{ mode = $(if ($case -in @('tunnel_fail', 'pairing')) { 'valid' } else { $case }); auth_mode = $authMode; port = $port; public_port = $publicPort; hashes = $hashes; tunnel_fail = ($case -eq 'tunnel_fail'); node = $NodePath; parent_path = $plainPath }
        $scenario | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $project 'scenario.json')
        $occupied = $null
        $failed = $false
        $output = ''
        try {
            if ($case -eq 'occupied') { $occupied = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $publicPort); $occupied.Start() }
            $output = & (Join-Path $scriptsDirectory 'Start-DasOffice.ps1') -PythonPath $PythonPath -NodePath $NodePath -ProjectRoot $project -CredentialPath $store -StartTunnel -CloudflaredPath $PythonPath -Port $port -PublicPort $publicPort -ReadyTimeoutSeconds 2 *>&1 | Out-String
        } catch { $failed = $true }
        finally { if ($occupied) { $occupied.Stop() } }
        Assert-Check ($failed -eq ($case -notin @('valid', 'pairing'))) "Unexpected outcome in case $case."
        foreach ($value in $dummy.Values) { Assert-Check (-not $output.Contains($value)) 'A dummy secret leaked to launcher output.' }
        foreach ($field in $fields) { Assert-Check ([Environment]::GetEnvironmentVariable($field, 'Process') -ceq 'inherited-test-sentinel') 'Parent environment changed.' }
        Assert-Check ([Environment]::GetEnvironmentVariable('PATH', 'Process') -ceq $plainPath) 'Parent PATH changed.'
        $tunnelResult = Join-Path $project 'tunnel-result.json'
        Assert-Check ((Test-Path -LiteralPath $tunnelResult) -eq ($case -in @('valid', 'tunnel_fail', 'pairing'))) 'Tunnel started without authenticated readiness.'
        foreach ($file in @('server-result.json', 'tunnel-result.json')) {
            $path = Join-Path $project $file
            if (-not (Test-Path -LiteralPath $path)) { continue }
            $result = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
            foreach ($property in $result.PSObject.Properties) {
                if ($property.Name -eq 'pid') { Assert-Check (-not (Get-Process -Id $property.Value -ErrorAction SilentlyContinue)) 'A child was left running.' }
                else { Assert-Check ($property.Value -eq $true) "Child contract failed: $($property.Name)." }
            }
        }
        Write-Host "PASS: $case"
    }
    foreach ($file in @('server-result.json', 'tunnel-result.json')) {
        $path = Join-Path $project $file
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
    }
    $fakeNodeDirectory = Join-Path $scratch 'fake-node'
    New-Item -ItemType Directory -Path $fakeNodeDirectory | Out-Null
    $notExecutable = Join-Path $fakeNodeDirectory 'node.exe'
    'This is not an executable.' | Set-Content -LiteralPath $notExecutable
    $notExePath = Join-Path $scratch 'not-node.txt'
    'This is not an executable.' | Set-Content -LiteralPath $notExePath
    foreach ($invalidNode in @('', (Join-Path $scratch 'missing.exe'), $project, $notExecutable, $notExePath)) {
        $failed = $false
        try {
            & (Join-Path $scriptsDirectory 'Start-DasOffice.ps1') -PythonPath $PythonPath -NodePath $invalidNode -ProjectRoot $project -CredentialPath $store -StartTunnel -CloudflaredPath $PythonPath -Port $port -PublicPort $publicPort -ReadyTimeoutSeconds 2 | Out-Null
        } catch { $failed = $true }
        Assert-Check $failed 'Invalid NodePath must be rejected before launching children.'
        Assert-Check (-not (Test-Path -LiteralPath (Join-Path $project 'server-result.json'))) 'Invalid NodePath started a server.'
        Assert-Check (-not (Test-Path -LiteralPath (Join-Path $project 'tunnel-result.json'))) 'Invalid NodePath started a tunnel.'
        Assert-Check ([Environment]::GetEnvironmentVariable('PATH', 'Process') -ceq $plainPath) 'Invalid NodePath changed parent PATH.'
        foreach ($field in $fields) { Assert-Check ([Environment]::GetEnvironmentVariable($field, 'Process') -ceq 'inherited-test-sentinel') 'Invalid NodePath changed parent credentials.' }
    }
    Write-Host 'PASS: Node-free parent, server-only Node discovery, unchanged tunnel PATH, five invalid NodePath boundaries.'
    Write-Host 'PASS: DPAPI round trip, separate updates, restricted ACL, child-only environments, hidden token output and cleanup.'
} finally {
    [Environment]::SetEnvironmentVariable('PATH', $originalPath, 'Process')
    foreach ($field in $fields) { [Environment]::SetEnvironmentVariable($field, $savedEnvironment[$field], 'Process') }
    # The only recursive removal is this freshly generated, verified temporary directory.
    $resolvedScratch = [IO.Path]::GetFullPath($scratch)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolvedScratch.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedScratch) -match '^DAS Office test [a-f0-9]{32}$' -and (Test-Path -LiteralPath $resolvedScratch)) {
        Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
    }
}
