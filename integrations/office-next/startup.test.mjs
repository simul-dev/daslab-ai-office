import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const helper=fileURLToPath(new URL('./Wait-OfficeNextPostgres.ps1',import.meta.url)).replaceAll("'","''");
const windows={skip:process.platform!=='win32'};
function check(script) {
  return execFileSync('pwsh',['-NoProfile','-NonInteractive','-Command',
    `$ErrorActionPreference='Stop'; . '${helper}'; ${script}`],{encoding:'utf8',windowsHide:true,timeout:10000});
}
const ownedListener=`
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) [pscustomobject]@{OwningProcess=100;LocalAddress='127.0.0.1'} }
function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) [pscustomobject]@{Name='postgres.exe';CommandLine='postgres.exe -D C:\\trial-db -h 127.0.0.1'} }
`;
test('launcher waits through recovery until the owned database accepts connections',windows,()=>{
  const result=check(`${ownedListener}
    $script:calls=0
    function Test-OfficeNextPostgresReady { param($NodePath,$ConfigPath,$RuntimePath) $script:calls++; return $script:calls -ge 2 }
    Wait-OfficeNextPostgres -DbData 'C:\\trial-db' -NodePath 'unused' -ConfigPath 'unused' -RuntimePath 'unused' -TimeoutSeconds 2
    if($script:calls -ne 2){throw 'Did not wait through recovery'}
    'ready'
  `);
  assert.match(result,/ready/);
});
test('launcher refuses an unrelated or publicly bound database before a probe',windows,()=>{
  const result=check(`${ownedListener}
    function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) [pscustomobject]@{OwningProcess=100;LocalAddress='0.0.0.0'} }
    function Test-OfficeNextPostgresReady { throw 'Must not probe another database' }
    try { Wait-OfficeNextPostgres -DbData 'C:\\trial-db' -NodePath 'unused' -ConfigPath 'unused' -RuntimePath 'unused'; throw 'Unexpected acceptance' }
    catch { if($_.Exception.Message -notlike 'Port 54329 is not*'){throw}; 'refused' }
  `);
  assert.match(result,/refused/);
});
test('a different database sharing the directory prefix is not treated as owned',windows,()=>{
  const result=check(`${ownedListener}
    function Get-CimInstance { param($ClassName,$Filter,$ErrorAction) [pscustomobject]@{Name='postgres.exe';CommandLine='postgres.exe -D C:\\trial-db-other -h 127.0.0.1'} }
    function Test-OfficeNextPostgresReady { throw 'Must not probe another database' }
    try { Wait-OfficeNextPostgres -DbData 'C:\\trial-db' -NodePath 'unused' -ConfigPath 'unused' -RuntimePath 'unused'; throw 'Unexpected acceptance' }
    catch { if($_.Exception.Message -notlike 'Port 54329 is not*'){throw}; 'refused' }
  `);
  assert.match(result,/refused/);
});
test('launcher has a bounded wait when the isolated database never becomes ready',windows,()=>{
  const result=check(`${ownedListener}
    function Test-OfficeNextPostgresReady { param($NodePath,$ConfigPath,$RuntimePath) return $false }
    try { Wait-OfficeNextPostgres -DbData 'C:\\trial-db' -NodePath 'unused' -ConfigPath 'unused' -RuntimePath 'unused' -TimeoutSeconds 1; throw 'Unexpected acceptance' }
    catch { if($_.Exception.Message -notlike '*did not become ready*'){throw}; 'timeout' }
  `);
  assert.match(result,/timeout/);
});
