import test from 'node:test';
import assert from 'node:assert/strict';
import { activityFor, assignCharacterIds, validateSnapshot, safeLink, selectionForSnapshot } from '../src/snapshot.mjs';

test('assigned and in-progress issues alone do not claim agent execution', () => {
  assert.equal(activityFor({ status: 'idle', currentIssue: { status: 'in_progress' } }).working, false);
  assert.equal(activityFor({ status: 'active' }).working, false);
  assert.equal(activityFor({ status: 'idle', activeRun: { status: 'queued' } }).working, false);
  assert.equal(activityFor({ status: 'idle', activeRun: { status: 'running' } }).working, true);
});

test('connection failure stops working animation and removes unsupported blocker claim', () => {
  assert.deepEqual(activityFor({ status: 'running', blockedReason: 'old blocker' }, false), { key: 'unknown', label: '연결 확인 필요', working: false, attention: false });
});

test('blocked issues and manager input are separate from idle staff', () => {
  assert.equal(activityFor({ status: 'idle', currentIssue: { status: 'blocked' } }).attention, true);
  assert.equal(activityFor({ status: 'idle', blockedReason: 'Missing data' }).attention, true);
  assert.equal(activityFor({ status: 'paused' }).key, 'paused');
  assert.equal(activityFor({ status: 'idle', currentIssue: { status: 'in_review' } }).key, 'review');
});

test('only bridge-verified dependencies are team waiting, with no owner attention or working animation', () => {
  const currentIssue={status:'blocked',waitingOnTeam:true};
  assert.deepEqual(activityFor({status:'waiting_on_team',currentIssue}),{key:'team_wait',label:'팀 작업 대기',working:false,attention:false});
  assert.equal(activityFor({status:'waiting_on_team',currentIssue:{status:'blocked'}}).attention,true);
  assert.equal(activityFor({status:'error',currentIssue}).attention,true);
  assert.equal(activityFor({status:'paused',currentIssue}).key,'paused');
  assert.equal(activityFor({status:'waiting_on_team',currentIssue},false).key,'unknown');
});

test('validates full snapshots and rejects ambiguous staff identity', () => {
  const valid = { observedAt: '2026-10-01T01:00:00Z', agents: [{ id: 'a', name: 'PM' }], issues: [] };
  assert.equal(validateSnapshot(valid), valid);
  assert.throws(() => validateSnapshot({ ...valid, agents: [...valid.agents, ...valid.agents] }));
  assert.throws(() => validateSnapshot({ ...valid, observedAt: '' }));
  assert.throws(() => validateSnapshot({ ...valid, issues: null }));
});

test('character identity and appearance are stable across reordered snapshots', () => {
  const agents = [{ id: 'pm' }, { id: 'research' }, { id: 'qa' }];
  assert.deepEqual(assignCharacterIds(agents), assignCharacterIds(agents.toReversed()));
  assert.equal(new Set(assignCharacterIds(agents).values()).size, 3);
});

test('result links do not permit script or cross-origin protocol-relative URLs', () => {
  assert.equal(safeLink('javascript:alert(1)'), undefined);
  assert.equal(safeLink('//evil.example'), undefined);
  assert.equal(safeLink('/artifacts/123'), '/artifacts/123');
  assert.equal(safeLink('https://example.com/result'), 'https://example.com/result');
});

test('initial command recipient is pinned across live roster reorder', () => {
  const assistant={id:'assistant'}, researcher={id:'researcher'};
  let selected=selectionForSnapshot(null,[assistant,researcher]);
  assert.equal(selected,'assistant');
  selected=selectionForSnapshot(selected,[researcher,assistant]);
  assert.equal(selected,'assistant');
  assert.equal([researcher,assistant].find(agent=>agent.id===selected),assistant);
});

test('missing selected recipient does not retarget an existing draft to another employee', () => {
  const remaining=[{id:'researcher'}];
  const selected=selectionForSnapshot('assistant',remaining);
  assert.equal(selected,'assistant');
  assert.equal(remaining.find(agent=>agent.id===selected),undefined);
  assert.equal(selectionForSnapshot(selected,[]),'assistant');
  assert.equal(selectionForSnapshot(selected,[{id:'assistant'},...remaining]),'assistant');
});

test('an initially empty roster waits for its first real employee without overriding explicit selection', () => {
  assert.equal(selectionForSnapshot(null,[]),null);
  assert.equal(selectionForSnapshot(null,[{id:'assistant'}]),'assistant');
  assert.equal(selectionForSnapshot('researcher',[{id:'assistant'},{id:'researcher'}]),'researcher');
});
