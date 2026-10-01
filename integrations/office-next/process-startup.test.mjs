import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const helper=fileURLToPath(new URL('./OfficeNext-Process.ps1',import.meta.url)).replaceAll("'","''");
const launcher=fileURLToPath(new URL('./Start-OfficeNext.ps1',import.meta.url)).replaceAll("'","''");
const windows={skip:process.platform!=='win32'};
function check(script) {
  return execFileSync('pwsh',['-NoProfile','-NonInteractive','-Command',
    `$ErrorActionPreference='Stop'; . '${helper}'; ${script}`],{encoding:'utf8',windowsHide:true,timeout:12000});
}
const owned=`
$nodeExe='C:\\Program Files\\node\\node.exe'
$wanted=@('C:\\isolated trial\\bridge.mjs','C:\\isolated trial\\office.json')
$process=[pscustomobject]@{ExecutablePath=$nodeExe;CommandLine='"C:\\Program Files\\node\\node.exe" "C:\\isolated trial\\bridge.mjs" "C:\\isolated trial\\office.json"'}
`;

test('ownership requires the executable and complete argument sequence, preserving quoted paths',windows,()=>{
  const result=check(`${owned}
    if(-not (Test-OfficeNextOwnedProcess -Process $process -NodePath $nodeExe -Arguments $wanted)){throw 'Rejected owned process'}
    foreach($wrong in @(
      [pscustomobject]@{ExecutablePath='C:\\other\\node.exe';CommandLine=$process.CommandLine},
      [pscustomobject]@{ExecutablePath=$nodeExe;CommandLine=$process.CommandLine.Replace('bridge.mjs','other.mjs')},
      [pscustomobject]@{ExecutablePath=$nodeExe;CommandLine=$process.CommandLine.Replace('office.json','office.json.other')},
      [pscustomobject]@{ExecutablePath=$nodeExe;CommandLine=$process.CommandLine+' --extra'}
    )){if(Test-OfficeNextOwnedProcess -Process $wrong -NodePath $nodeExe -Arguments $wanted){throw 'Accepted a different process'}}
    $quoted=ConvertTo-OfficeNextArgumentString -Arguments @($nodeExe,'C:\\path with spaces\\')
    $parsed=@(Get-OfficeNextProcessArguments -CommandLine $quoted)
    if($parsed[1] -ne 'C:\\path with spaces\\'){throw 'Changed quoted trailing slash'}
    'exact'
  `);
  assert.match(result,/exact/);
});

test('an unknown listener is refused before any readiness request and nothing is stopped',windows,()=>{
  const result=check(`${owned}
    function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) [pscustomobject]@{OwningProcess=100;LocalAddress='127.0.0.1'} }
    function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) [pscustomobject]@{ExecutablePath=$nodeExe;CommandLine=$process.CommandLine.Replace('office.json','foreign.json')} }
    function Test-OfficeNextJsonReady { throw 'Must not probe unknown process' }
    function Stop-Process { throw 'Must not stop anything' }
    try { Wait-OfficeNextService -Label 'office-next' -Port 8790 -NodePath $nodeExe -Arguments $wanted; throw 'Unexpected acceptance' }
    catch { if($_.Exception.Message -notlike '*unverified process*'){throw}; 'refused' }
  `);
  assert.match(result,/refused/);
});

test('known endpoints must return the expected JSON shape rather than a listening socket or HTML',windows,()=>{
  const result=check(`
    function Invoke-WebRequest { param($Uri,$TimeoutSec,$MaximumRedirection) $script:response }
    $script:response=[pscustomobject]@{StatusCode=200;Headers=@{'Content-Type'='text/html'};Content='<html>login</html>'}
    if(Test-OfficeNextJsonReady -Label paperclip -Port 3101){throw 'Accepted HTML'}
    $script:response.Headers['Content-Type']='application/json'
    $script:response.Content='{"status":"ok"}'
    if(Test-OfficeNextJsonReady -Label paperclip -Port 3101){throw 'Accepted another JSON service'}
    $script:response.Content='[{"id":"company","name":"Trial"}]'
    if(-not (Test-OfficeNextJsonReady -Label paperclip -Port 3101)){throw 'Rejected company list'}
    $script:response.Content='{"mode":"pilot","company":{"id":"company"},"agents":[],"issues":[]}'
    if(-not (Test-OfficeNextJsonReady -Label office-next -Port 8790)){throw 'Rejected bridge snapshot'}
    $script:response.StatusCode=503
    if(Test-OfficeNextJsonReady -Label office-next -Port 8790){throw 'Accepted failed endpoint'}
    'validated'
  `);
  assert.match(result,/validated/);
});

test('service readiness waits for JSON and times out without starting duplicate work',windows,()=>{
  const result=check(`${owned}
    function Get-OfficeNextOwnedListener { param($Label,$Port,$NodePath,$Arguments,$ExpectedProcessId) 100 }
    $script:checks=0
    function Test-OfficeNextJsonReady { param($Label,$Port) $script:checks++; return $script:checks -ge 2 }
    $ready=Wait-OfficeNextService -Label office-next -Port 8790 -NodePath $nodeExe -Arguments $wanted -TimeoutSeconds 240
    if($ready -ne 100 -or $script:checks -ne 2){throw 'Did not wait for readiness'}
    function Test-OfficeNextJsonReady { param($Label,$Port) $false }
    function Start-Process { throw 'Must not start a duplicate' }
    try { Wait-OfficeNextService -Label office-next -Port 8790 -NodePath $nodeExe -Arguments $wanted -TimeoutSeconds 1; throw 'Unexpected acceptance' }
    catch { if($_.Exception.Message -notlike '*expected JSON response*'){throw}; 'bounded' }
  `);
  assert.match(result,/bounded/);
});

test('launcher reuses verified listeners and matching recorded processes that are still starting',windows,()=>{
  const result=check(`${owned}
    $ast=[System.Management.Automation.Language.Parser]::ParseFile('${launcher}',[ref]$null,[ref]$null)
    $definition=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Start-OwnedProcess'},$true)
    Invoke-Expression $definition.Extent.Text
    $nextRoot='C:\\isolated trial'; $logs='C:\\isolated trial\\logs'
    $script:listenerId=100
    function Get-OfficeNextOwnedListener { param($Label,$Port,$NodePath,$Arguments) $script:listenerId }
    function Wait-OfficeNextService { param($Label,$Port,$NodePath,$Arguments,$ExpectedProcessId) if($ExpectedProcessId -ne 100){throw 'Wrong process'}; 100 }
    function Start-Process { throw 'Must not start a duplicate' }
    function Test-Path { param($LiteralPath) $true }
    function Get-Content { param($LiteralPath,[switch]$Raw) '{"pid":100}' }
    function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) $process }
    Start-OwnedProcess office-next 8790 $wanted 'C:\\isolated trial'
    $script:listenerId=0
    Start-OwnedProcess office-next 8790 $wanted 'C:\\isolated trial'
  `);
  assert.match(result,/left unchanged/);
  assert.match(result,/existing startup reused/);
});
