import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createServer} from 'node:http';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {attachWorkerWatchdog, inspectWindowsProcesses, sameProcess, stopReason,
  superviseWorker, terminateOwnedWindowsProcess} from './worker-watchdog.mjs';

const parent = {pid: 100, startedTicks: '100000', executable: 'C:\\runtime\\node.exe'};
const child = {pid: 101, startedTicks: '100010', executable: 'C:\\runtime\\codex.exe'};
const config = {parent, child, deadlineAt: 1000, pollMs: 1};
const state = {parent, child, expectedParent: parent, expectedChild: child,
  parentDisconnected: false, now: 500, deadlineAt: 1000, run: {status: 'running'}};

test('PID reuse or executable change never authorizes a kill', () => {
  assert.equal(sameProcess({...child, startedTicks: 'other'}, child), false);
  assert.equal(stopReason({...state, child: {...child, startedTicks: 'other'}}), 'child-identity-changed');
  assert.equal(stopReason({...state, child: null, parent: null}), 'child-exited');
  assert.equal(stopReason({...state, child: {...child, executable: 'C:\\other.exe'}}), 'child-identity-changed');
});

test('parent identity, deadline and actual terminal run states trigger stop', () => {
  assert.equal(stopReason({...state, parent: {...parent, startedTicks: 'reused'}}), 'parent-exited');
  assert.equal(stopReason({...state, parentDisconnected: true}), 'parent-exited');
  assert.equal(stopReason({...state, now: 1000}), 'deadline');
  assert.equal(stopReason({...state, run: {status: 'cancelled'}}), 'run-cancelled');
  assert.equal(stopReason({...state, run: {status: 'running', resultJson: {
    executionCancellation: {state: 'requested'}}}}), 'run-cancel-requested');
  assert.equal(stopReason({...state, run: {status: 'queued'}}), null);
});

test('network failure does not cause a speculative kill; bounded deadline still stops work', async () => {
  let clock = 0, kills = 0;
  const result = await superviseWorker({...config, deadlineAt: 2}, {
    inspect: async () => [parent, child], now: () => clock, parentDisconnected: () => false,
    fetchRun: async () => {throw new Error('offline');}, sleep: async () => {clock++;},
    terminate: async target => {assert.equal(target, child); kills++; return {state: 'terminated'};},
  });
  assert.equal(kills, 1);
  assert.deepEqual(result, {reason: 'deadline', termination: 'terminated'});
});

test('normal exit and observation failures never kill an unverified process', async () => {
  const deps = {now: () => 0, parentDisconnected: () => false, fetchRun: async () => null,
    sleep: async () => {}, terminate: async () => {assert.fail('unexpected kill');}};
  assert.deepEqual(await superviseWorker(config, {...deps, inspect: async () => [parent]}),
    {reason: 'child-exited', termination: 'not-needed'});
  let inspections = 0;
  assert.deepEqual(await superviseWorker(config, {...deps, inspect: async () => {
    if (++inspections < 4) throw new Error('temporarily unavailable');
    return [parent];
  }}), {reason: 'child-exited', termination: 'not-needed'});
});

test('Windows detached watchdog survives parent loss and stops only its recorded child tree',
  {skip: process.platform !== 'win32', timeout: 45000}, async () => {
    const dir = await mkdtemp(path.join(tmpdir(), 'office-watchdog-test-'));
    const launchedAt = Date.now();
    const fixture = spawn(process.execPath, ['-e', `
      const {spawn}=require('node:child_process');
      const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{detached:true,windowsHide:true,stdio:'ignore'});
      child.unref();
      process.send({pid:child.pid});
      process.on('message',m=>{if(m==='exit')process.exit(0)});
      setInterval(()=>{},1000);
    `], {windowsHide: true, stdio: ['ignore', 'ignore', 'ignore', 'ipc']});
    let worker, watcher;
    try {
      const {pid} = await new Promise((resolve, reject) => {fixture.once('message', resolve); fixture.once('error', reject);});
      const [identity] = await inspectWindowsProcesses([pid]);
      worker = identity;
      const receiptPath = path.join(dir, 'receipt.json');
      watcher = await attachWorkerWatchdog({child: {pid, exitCode: null}, executable: process.execPath,
        launchedAt, deadlineAt: Date.now() + 20000, parentPid: fixture.pid, receiptPath, pollMs: 300,
        env: {...process.env, PAPERCLIP_API_URL: 'http://127.0.0.1:1', PAPERCLIP_API_KEY: 'fixture-only-not-a-secret',
          PAPERCLIP_RUN_ID: '10000000-0000-4000-8000-000000000001',
          PAPERCLIP_AGENT_ID: '10000000-0000-4000-8000-000000000002',
          PAPERCLIP_COMPANY_ID: '10000000-0000-4000-8000-000000000003'}});
      fixture.send('exit');
      let receipt;
      for (let attempt = 0; attempt < 50; attempt++) {
        try {receipt = JSON.parse(await readFile(receiptPath, 'utf8')); break;} catch {await delay(250);}
      }
      assert.ok(['parent-exited', 'child-exited'].includes(receipt?.reason));
      assert.ok(['terminated', 'gone', 'not-needed'].includes(receipt?.termination));
      assert.equal((await inspectWindowsProcesses([pid])).length, 0);
      assert.equal(JSON.stringify(receipt).includes('fixture-only-not-a-secret'), false);
    } finally {
      if (worker) await terminateOwnedWindowsProcess(worker).catch(() => {});
      fixture.kill();
      if (watcher?.connected) watcher.disconnect();
      assert.equal(path.dirname(path.resolve(dir)), path.resolve(tmpdir()));
      assert.ok(path.basename(dir).startsWith('office-watchdog-test-'));
      await rm(dir, {recursive: true, force: true});
    }
  });

test('Windows cancellation kills the verified worker and its live descendant while parent remains alive',
  {skip: process.platform !== 'win32', timeout: 45000}, async () => {
    const dir = await mkdtemp(path.join(tmpdir(), 'office-watchdog-test-'));
    const runId = '20000000-0000-4000-8000-000000000001';
    const agentId = '20000000-0000-4000-8000-000000000002';
    const companyId = '20000000-0000-4000-8000-000000000003';
    let cancelled = false;
    const server = createServer((_req, response) => {
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify({id: runId, agentId, companyId, status: cancelled ? 'cancelled' : 'running'}));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const launchedAt = Date.now();
    const worker = spawn(process.execPath, ['-e', `
      const {spawn}=require('node:child_process');
      const nested=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{windowsHide:true,stdio:'ignore'});
      process.send({pid:nested.pid}); setInterval(()=>{},1000);
    `], {windowsHide: true, stdio: ['ignore', 'ignore', 'ignore', 'ipc']});
    let identity, nestedIdentity, watcher;
    try {
      const nested = await new Promise((resolve, reject) => {worker.once('message', resolve); worker.once('error', reject);});
      [identity] = await inspectWindowsProcesses([worker.pid]);
      [nestedIdentity] = await inspectWindowsProcesses([nested.pid]);
      assert.ok(identity && nestedIdentity);
      const changedIdentity = {...identity, startedTicks: String(BigInt(identity.startedTicks) + 1n)};
      assert.equal((await terminateOwnedWindowsProcess(changedIdentity)).state, 'identity-changed');
      assert.equal((await inspectWindowsProcesses([worker.pid])).length, 1);
      const receiptPath = path.join(dir, 'receipt.json');
      watcher = await attachWorkerWatchdog({child: worker, executable: process.execPath, launchedAt,
        deadlineAt: Date.now() + 20000, receiptPath, pollMs: 300,
        env: {...process.env, PAPERCLIP_API_URL: `http://127.0.0.1:${server.address().port}`,
          PAPERCLIP_API_KEY: 'fixture-only-not-a-secret', PAPERCLIP_RUN_ID: runId,
          PAPERCLIP_AGENT_ID: agentId, PAPERCLIP_COMPANY_ID: companyId}});
      cancelled = true;
      let receipt;
      for (let attempt = 0; attempt < 60; attempt++) {
        try {receipt = JSON.parse(await readFile(receiptPath, 'utf8')); break;} catch {await delay(250);}
      }
      assert.equal(receipt?.reason, 'run-cancelled');
      assert.equal(receipt?.termination, 'terminated');
      assert.equal((await inspectWindowsProcesses([worker.pid, nested.pid])).length, 0);
      assert.equal((await inspectWindowsProcesses([process.pid])).length, 1);
    } finally {
      if (identity) await terminateOwnedWindowsProcess(identity).catch(() => {});
      if (nestedIdentity) await terminateOwnedWindowsProcess(nestedIdentity).catch(() => {});
      if (watcher?.connected) watcher.disconnect();
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
      assert.equal(path.dirname(path.resolve(dir)), path.resolve(tmpdir()));
      assert.ok(path.basename(dir).startsWith('office-watchdog-test-'));
      await rm(dir, {recursive: true, force: true});
    }
  });
