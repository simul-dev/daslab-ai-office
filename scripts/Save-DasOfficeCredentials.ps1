#requires -Version 7.2
<# Run interactively in PowerShell 7 on Windows. Values are never command arguments. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('GitHub', 'Tunnel')][string]$Kind,
    [string]$CredentialPath = (Join-Path $env:LOCALAPPDATA 'DASLab\ai-office\credentials.clixml')
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'Windows DPAPI is required.' }
$CredentialPath = [IO.Path]::GetFullPath($CredentialPath)
$directory = Split-Path -Parent $CredentialPath
$temporary = Join-Path $directory ([guid]::NewGuid().ToString('N') + '.clixml')
$credentials = @{ Version = 1 }
try {
    if (Test-Path -LiteralPath $CredentialPath) {
        $credentials = Import-Clixml -LiteralPath $CredentialPath
        if ($credentials -isnot [hashtable] -or $credentials.Version -ne 1) { throw 'Invalid credential store.' }
    }
    foreach ($key in $credentials.Keys) {
        if ($key -notin @('Version', 'GitHubClientId', 'GitHubClientSecret', 'TunnelToken') -or
            ($key -ne 'Version' -and $credentials[$key] -isnot [Security.SecureString])) {
            throw 'Invalid credential store.'
        }
    }
    $fields = if ($Kind -eq 'GitHub') { @('GitHubClientId', 'GitHubClientSecret') } else { @('TunnelToken') }
    foreach ($field in $fields) {
        $prompt = if ($field -eq 'TunnelToken') { 'Tunnel token or copied cloudflared.exe service install command' } else { $field }
        $value = Read-Host -Prompt $prompt -AsSecureString
        if ($field -eq 'TunnelToken') {
            # Parse the dashboard's one exact command shape as data. Never execute pasted text.
            if ($value.Length -gt 8240) { $value.Dispose(); throw 'Invalid tunnel input.' }
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
            $match = $null
            try {
                $match = [regex]::Match([Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer),
                    '\A(?:cloudflared\.exe service install )?([A-Za-z0-9+/_-]{1,8192}={0,2})\z')
                if (-not $match.Success) { throw 'Paste only the tunnel token or the exact cloudflared.exe service install command, without extra whitespace or masked characters.' }
                $token = [Security.SecureString]::new()
                foreach ($character in $match.Groups[1].Value.ToCharArray()) { $token.AppendChar($character) }
            } finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
                $match = $null
                $value.Dispose()
            }
            $value = $token
        }
        $invalid = $value.Length -lt 1 -or $value.Length -gt 8192
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
        try {
            for ($index = 0; -not $invalid -and $index -lt $value.Length; $index++) {
                $character = [char]([int][Runtime.InteropServices.Marshal]::ReadInt16($pointer, 2 * $index) -band 65535)
                $invalid = [char]::IsWhiteSpace($character) -or [char]::IsControl($character)
            }
        } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        if ($invalid) { $value.Dispose(); throw 'Input canceled or invalid: use 1-8192 characters without whitespace or control characters.' }
        if ($credentials.ContainsKey($field)) { $credentials[$field].Dispose() }
        $credentials[$field] = $value
    }
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    if ((Get-Item -LiteralPath $directory).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'The credential directory must not be a link.'
    }
    # Only this Windows account and SYSTEM inherit access to the encrypted file.
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = Get-Acl -LiteralPath $directory
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($existingRule in @($acl.Access)) { [void]$acl.RemoveAccessRuleSpecific($existingRule) }
    foreach ($identity in @($sid, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    [IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]::new($directory), $acl)
    # Export-Clixml uses per-user Windows DPAPI for every SecureString; no AES key file.
    $credentials | Export-Clixml -LiteralPath $temporary -Depth 3
    [IO.File]::Move($temporary, $CredentialPath, $true)
    Write-Host "Encrypted $Kind credentials saved for this Windows account."
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    foreach ($value in $credentials.Values) {
        if ($value -is [Security.SecureString]) { $value.Dispose() }
    }
}
