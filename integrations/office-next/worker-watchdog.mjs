// Own only the freshly spawned worker tree. No credentials in argv or receipts.
import {spawn, execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {realpath, mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const execFileAsync = promisify(execFile);
const thisFile = fileURLToPath(import.meta.url);
const terminalStatuses = new Set(['cancelled', 'failed', 'timed_out', 'succeeded']);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const psQuote = value => `'${String(value).replaceAll("'", "''")}'`;
const normalizedPath = value => path.win32.normalize(value).toLowerCase();
const validPid = value => Number.isSafeInteger(value) && value > 0;

export function sameProcess(actual, expected) {
  return Boolean(actual && expected && actual.pid === expected.pid &&
    actual.startedTicks === expected.startedTicks &&
    normalizedPath(actual.executable) === normalizedPath(expected.executable));
}

export function stopReason({child, parent, expectedChild, expectedParent, parentDisconnected,
  now, deadlineAt, run}) {
  if (!child) return 'child-exited';
  if (!sameProcess(child, expectedChild)) return 'child-identity-changed';
  if (parentDisconnected || !sameProcess(parent, expectedParent)) return 'parent-exited';
  if (now >= deadlineAt) return 'deadline';
  if (run && terminalStatuses.has(run.status)) return `run-${run.status}`;
  if (run?.resultJson?.executionCancellation?.state === 'requested') return 'run-cancel-requested';
  return null;
}

function systemEnv(env = process.env) {
  const names = new Set(['PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'USERPROFILE',
    'LOCALAPPDATA', 'APPDATA', 'TEMP', 'TMP', 'HOMEDRIVE', 'HOMEPATH']);
  return Object.fromEntries(Object.entries(env).filter(([key]) => names.has(key.toUpperCase())));
}

export function defaultPowerShellPath(env = process.env) {
  return path.join(env.USERPROFILE, '.cache', 'codex-runtimes', 'codex-primary-runtime',
    'dependencies', 'native', 'powershell', 'pwsh.exe');
}

async function powershellJson(script, powershellPath) {
  const {stdout} = await execFileAsync(powershellPath,
    ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
    {windowsHide: true, env: systemEnv(), timeout: 12000, maxBuffer: 128 * 1024});
  return JSON.parse(stdout.replace(/^\uFEFF/, '').trim());
}

export async function inspectWindowsProcesses(pids, powershellPath = defaultPowerShellPath()) {
  if (process.platform !== 'win32' || !pids.every(validPid)) throw new Error('Windows process IDs required.');
  // A process handle's start time prevents a recycled PID from matching later.
  const script = `$ErrorActionPreference='Stop'; $rows=@(); foreach($wantedPid in @(${pids.join(',')})) {
    $p=$null; try {$p=[Diagnostics.Process]::GetProcessById($wantedPid)} catch [ArgumentException] {continue};
    try { $started=$p.StartTime.ToUniversalTime(); $image=$p.MainModule.FileName;
      $meta=Get-CimInstance Win32_Process -Filter ("ProcessId = " + $wantedPid);
      if($null -eq $meta -or $p.HasExited){continue};
      $rows += [pscustomobject]@{pid=$wantedPid; parentPid=[int]$meta.ParentProcessId;
        startedTicks=$started.Ticks.ToString(); startedAt=([DateTimeOffset]$started).ToUnixTimeMilliseconds();
        executable=$image}
    } finally {if($null -ne $p){$p.Dispose()}}
  }; ConvertTo-Json -InputObject @($rows) -Compress`;
  const result = await powershellJson(script, powershellPath);
  return Array.isArray(result) ? result : [result];
}

export async function terminateOwnedWindowsProcess(identity, powershellPath = defaultPowerShellPath()) {
  if (!validPid(identity.pid) || !/^\d+$/.test(identity.startedTicks) || !path.win32.isAbsolute(identity.executable)) {
    throw new Error('Invalid owned process identity.');
  }
  // Revalidate using the process handle immediately before .NET's tree kill.
  // This needs PowerShell 7 / modern .NET, and does not elevate privileges.
  const script = `$ErrorActionPreference='Stop'; $p=$null;
    try {$p=[Diagnostics.Process]::GetProcessById(${identity.pid})}
    catch [ArgumentException] {Write-Output '{"state":"gone"}'; exit 0};
    try {
      if($p.StartTime.ToUniversalTime().Ticks.ToString() -ne ${psQuote(identity.startedTicks)} -or
        ![StringComparer]::OrdinalIgnoreCase.Equals($p.MainModule.FileName,${psQuote(identity.executable)})) {
        Write-Output '{"state":"identity-changed"}'; exit 0
      };
      $p.Kill($true);
      if(!$p.WaitForExit(8000)){throw 'Owned worker termination did not settle'};
      Write-Output '{"state":"terminated"}'
    } finally {if($null -ne $p){$p.Dispose()}}`;
  return powershellJson(script, powershellPath);
}

export async function superviseWorker(config, deps) {
  let run = null;
  const finish = async reason => {
    try {
      // The native terminator rechecks start time and image using its own handle.
      const stopped = await deps.terminate(config.child);
      return {reason, termination: stopped.state};
    } catch {
      return {reason, termination: 'failed'};
    }
  };
  for (;;) {
    let rows;
    try {
      rows = await deps.inspect([config.parent.pid, config.child.pid]);
    } catch {
      // A transient CIM error must not disable lifetime control. The separate
      // handle-based terminator still verifies identity before touching a PID.
      if (deps.parentDisconnected()) return finish('parent-exited');
      if (deps.now() >= config.deadlineAt) return finish('deadline');
      try {
        run = await deps.fetchRun();
        if (terminalStatuses.has(run?.status)) return finish(`run-${run.status}`);
        if (run?.resultJson?.executionCancellation?.state === 'requested') return finish('run-cancel-requested');
      } catch {run = null;}
      await deps.sleep(config.pollMs);
      continue;
    }
    let reason = stopReason({child: rows.find(row => row.pid === config.child.pid),
      parent: rows.find(row => row.pid === config.parent.pid), expectedChild: config.child,
      expectedParent: config.parent, parentDisconnected: deps.parentDisconnected(),
      now: deps.now(), deadlineAt: config.deadlineAt, run});
    if (!reason) {
      try {run = await deps.fetchRun();} catch {run = null;}
      reason = stopReason({child: rows.find(row => row.pid === config.child.pid),
        parent: rows.find(row => row.pid === config.parent.pid), expectedChild: config.child,
        expectedParent: config.parent, parentDisconnected: deps.parentDisconnected(),
        now: deps.now(), deadlineAt: config.deadlineAt, run});
    }
    if (reason === 'child-exited' || reason === 'child-identity-changed') return {reason, termination: 'not-needed'};
    if (reason) return finish(reason);
    await deps.sleep(config.pollMs);
  }
}

export async function attachWorkerWatchdog({child, executable, launchedAt, deadlineAt,
  parentPid = process.pid, env = process.env, powershellPath = defaultPowerShellPath(env),
  receiptPath, pollMs = 2000}) {
  if (process.platform !== 'win32') throw new Error('This watchdog is for the Windows worker only.');
  if (!validPid(child.pid) || !validPid(parentPid)) throw new Error('Worker process was not started.');
  const rows = await inspectWindowsProcesses([parentPid, child.pid], powershellPath);
  const parent = rows.find(row => row.pid === parentPid);
  const worker = rows.find(row => row.pid === child.pid);
  if (!worker && child.exitCode !== null) return null;
  if (!parent || !worker || worker.parentPid !== parentPid ||
      worker.startedAt < launchedAt - 2000 || worker.startedAt > launchedAt + 30000 ||
      normalizedPath(await realpath(worker.executable)) !== normalizedPath(await realpath(executable))) {
    throw new Error('Cannot establish ownership of the newly spawned worker.');
  }
  if (!Number.isFinite(deadlineAt) || deadlineAt <= Date.now() || deadlineAt > Date.now() + 60 * 60 * 1000) {
    throw new Error('A future worker deadline within one hour is required.');
  }
  for (const key of ['PAPERCLIP_RUN_ID', 'PAPERCLIP_AGENT_ID', 'PAPERCLIP_COMPANY_ID']) {
    if (!uuid.test(env[key] ?? '')) throw new Error(`Missing valid ${key}.`);
  }
  if (!env.PAPERCLIP_API_KEY) throw new Error('Run-scoped authentication is required by the watchdog.');
  const base = new URL(env.PAPERCLIP_API_URL);
  if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(base.hostname)) {
    throw new Error('Watchdog API must be local HTTP.');
  }
  const config = {parent, child: worker, deadlineAt, pollMs: Math.max(250, Math.min(pollMs, 5000)),
    powershellPath, runId: env.PAPERCLIP_RUN_ID, agentId: env.PAPERCLIP_AGENT_ID,
    companyId: env.PAPERCLIP_COMPANY_ID,
    receiptPath: receiptPath ?? path.join(env.LOCALAPPDATA, 'DASLab', 'ai-office-next',
      'verification', `watchdog-${env.PAPERCLIP_RUN_ID}.json`)};
  const watcherEnv = {...systemEnv(env), PAPERCLIP_API_URL: base.origin,
    PAPERCLIP_API_KEY: env.PAPERCLIP_API_KEY};
  const watcher = spawn(process.execPath, [thisFile, '--watch', JSON.stringify(config)], {
    detached: true, windowsHide: true, env: watcherEnv, stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
  });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Worker watchdog did not become ready.')), 15000);
    watcher.once('error', () => {clearTimeout(timer); reject(new Error('Worker watchdog could not start.'));});
    watcher.once('exit', () => {clearTimeout(timer); reject(new Error('Worker watchdog exited before ready.'));});
    watcher.once('message', message => {
      if (message?.type === 'ready') {clearTimeout(timer); resolve();}
    });
  });
  watcher.unref();
  watcher.channel?.unref();
  return watcher;
}

async function main() {
  const config = JSON.parse(process.argv[3]);
  let disconnected = !process.connected;
  process.on('disconnect', () => {disconnected = true;});
  const base = new URL(process.env.PAPERCLIP_API_URL);
  if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(base.hostname) ||
      !uuid.test(config.runId) || !process.env.PAPERCLIP_API_KEY) throw new Error('Invalid watchdog context.');
  process.send?.({type: 'ready'});
  const result = await superviseWorker(config, {
    inspect: pids => inspectWindowsProcesses(pids, config.powershellPath),
    terminate: identity => terminateOwnedWindowsProcess(identity, config.powershellPath),
    now: Date.now, sleep: delay, parentDisconnected: () => disconnected,
    fetchRun: async () => {
      const response = await fetch(new URL(`/api/heartbeat-runs/${config.runId}`, base), {
        headers: {Authorization: `Bearer ${process.env.PAPERCLIP_API_KEY}`},
        signal: AbortSignal.timeout(1800),
      });
      if (!response.ok) throw new Error('Run state unavailable.');
      const run = await response.json();
      if (run.id !== config.runId || run.agentId !== config.agentId || run.companyId !== config.companyId) {
        throw new Error('Run state identity mismatch.');
      }
      return run;
    },
  });
  await mkdir(path.dirname(config.receiptPath), {recursive: true});
  await writeFile(config.receiptPath, JSON.stringify({runId: config.runId, childPid: config.child.pid,
    finishedAt: new Date().toISOString(), ...result}, null, 2));
  if (process.connected) process.disconnect();
  process.exitCode = ['failed', 'unverified'].includes(result.termination) ? 1 : 0;
}

if (process.argv[2] === '--watch' && path.resolve(process.argv[1]) === path.resolve(thisFile)) {
  main().catch(() => {process.exitCode = 1; if (process.connected) process.disconnect();});
}
