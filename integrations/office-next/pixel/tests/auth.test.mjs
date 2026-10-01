import test from 'node:test';
import assert from 'node:assert/strict';
import { clearLoginDraft, createOfficeSession, DRAFT_KEY, hasOfficeAccess, saveLoginDraft, SessionUnavailableError, takeLoginDraft, visibleLink } from '../src/auth.mjs';

const response = (status, value) => ({ status, ok: status >= 200 && status < 300, json: async () => value });
const authenticated = () => response(200, { mode: 'pairing', required: true, authenticated: true });
const draft = { agentId: 'assistant', instruction: '현재 업무를 보고해줘', attempt: { agentId: 'assistant', instruction: '현재 업무를 보고해줘', requestId: '0219c09a-a62e-4b43-95ce-999e0388a1fe' } };
const memoryStorage = () => {
  const values = new Map();
  return { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
};

test('only trusted local auth with enabled pairing and a canonical HTTPS origin exposes phone pairing', async () => {
  const source = { mode: 'local', required: false, authenticated: true, pairing_available: true, public_origin: 'https://ai-office.daslab.co.kr' };
  const allowed = createOfficeSession(async () => response(200, source));
  await allowed.check('127.0.0.1');
  assert.deepEqual(allowed.state, { phase: 'local', pairingAvailable: true, publicOrigin: source.public_origin });
  for (const changes of [
    { pairing_available: false }, { pairing_available: undefined }, { read_only: true },
    { public_origin: 'http://ai-office.daslab.co.kr' }, { public_origin: 'https://ai-office.daslab.co.kr/login' },
    { public_origin: 'https://user@ai-office.daslab.co.kr' }, { public_origin: 'https://ai-office.daslab.co.kr/' },
    { mode: 'pairing', required: true },
  ]) {
    const session = createOfficeSession(async () => response(200, { ...source, ...changes }));
    await session.check('127.0.0.1');
    assert.equal(session.state.pairingAvailable, undefined);
    assert.equal(session.state.publicOrigin, undefined);
  }
  const standalone = createOfficeSession(async () => response(404));
  await standalone.check('127.0.0.1');
  assert.equal(standalone.state.pairingAvailable, undefined);
});

test('only loopback may treat a missing auth endpoint as the standalone local office', async () => {
  for (const hostname of ['127.0.0.1', 'localhost', '[::1]']) {
    const session = createOfficeSession(async () => response(404));
    await session.check(hostname);
    assert.equal(session.state.phase, 'local');
  }
  const session = createOfficeSession(async () => response(404));
  await session.check('ai-office.daslab.co.kr');
  assert.equal(session.state.phase, 'error');
  assert.equal(hasOfficeAccess(session.state), false);
  await assert.rejects(session.request('/api/office/snapshot'), SessionUnavailableError);
});

test('unauthenticated, malformed and public local-mode responses never permit private requests', async () => {
  for (const value of [response(401), response(200, { mode: 'pairing', required: true, authenticated: false }), response(200, {}), response(200, { mode: 'local', required: false, authenticated: true })]) {
    let calls = 0;
    const session = createOfficeSession(async () => { calls += 1; return value; });
    await session.check('ai-office.daslab.co.kr');
    await assert.rejects(session.request('/api/office/issues', { method: 'POST', body: JSON.stringify(draft.attempt) }), SessionUnavailableError);
    assert.equal(calls, 1);
  }
});

test('auth check and command use same-origin cookies and both gateway request headers', async () => {
  const calls = [];
  const session = createOfficeSession(async (path, options) => { calls.push({ path, options }); return path === '/api/auth' ? authenticated() : response(201, { id: 'task-1' }); });
  await session.check('ai-office.daslab.co.kr');
  const result = await session.request('/api/office/issues', { method: 'POST', body: JSON.stringify(draft.attempt) });
  assert.equal(result.id, 'task-1');
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[1].options.credentials, 'same-origin');
  assert.deepEqual(calls[1].options.headers, { 'Content-Type': 'application/json', 'X-Office-Next': '1', 'X-DAS-Office': '1' });
  assert.deepEqual(JSON.parse(calls[1].options.body), draft.attempt);
});

test('a 401 rejects concurrent stale data, blocks subsequent commands, and never automatically retries after login', async () => {
  let finishStale;
  const calls = [];
  const session = createOfficeSession(async (path, options) => {
    calls.push(path);
    if (path === '/api/auth') return authenticated();
    if (path === '/api/office/snapshot') return new Promise((resolve) => { finishStale = resolve; });
    return response(401, { error: 'login required' });
  });
  await session.check('ai-office.daslab.co.kr');
  const staleRead = session.request('/api/office/snapshot');
  await assert.rejects(session.request('/api/office/issues', { method: 'POST', body: JSON.stringify(draft.attempt) }), SessionUnavailableError);
  assert.equal(session.state.phase, 'required');
  finishStale(response(200, { privateData: 'outdated result' }));
  await assert.rejects(staleRead, SessionUnavailableError);
  await assert.rejects(session.request('/api/office/issues', { method: 'POST' }), SessionUnavailableError);
  await session.check('ai-office.daslab.co.kr');
  assert.equal(session.state.phase, 'authenticated');
  assert.equal(calls.filter((path) => path === '/api/office/issues').length, 1);
});

test('logout uses the existing endpoint and immediately blocks any more private actions', async () => {
  const calls = [];
  let completeLogout;
  let authChecks = 0;
  const session = createOfficeSession(async (path, options) => {
    calls.push({ path, options });
    if (path === '/api/auth') return authChecks++ === 0 ? authenticated() : response(200, { mode: 'pairing', required: true, authenticated: false });
    return new Promise((resolve) => { completeLogout = resolve; });
  });
  await session.check('ai-office.daslab.co.kr');
  const pending = session.logout();
  assert.equal(session.state.phase, 'logging-out');
  await assert.rejects(session.request('/api/office/issues', { method: 'POST' }), SessionUnavailableError);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].path, '/auth/logout');
  assert.equal(calls[1].options.method, 'POST');
  assert.equal(calls[1].options.credentials, 'same-origin');
  assert.deepEqual(calls[1].options.headers, { 'Content-Type': 'application/json', 'X-DAS-Office': '1' });
  assert.equal(calls[1].options.body, '{}');
  completeLogout(response(200, { ok: true }));
  await pending;
  assert.equal(calls[2].path, '/api/auth');
  assert.equal(calls[2].options.credentials, 'same-origin');
  assert.equal(calls[2].options.cache, 'no-store');
  assert.equal(calls[2].options.redirect, 'error');
  assert.equal(session.state.phase, 'required');
});

test('the actual logout HTML redirect succeeds only after the auth endpoint confirms revocation', async () => {
  let authChecks = 0;
  const session = createOfficeSession(async (path) => {
    if (path === '/api/auth') return authChecks++ === 0 ? authenticated() : response(200, { mode: 'pairing', required: true, authenticated: false });
    return { status: 200, ok: true, redirected: true, url: 'https://ai-office.daslab.co.kr/login', json() { throw new Error('HTML is not JSON'); } };
  });
  await session.check('ai-office.daslab.co.kr');
  await session.logout();
  assert.equal(authChecks, 2);
  assert.equal(session.state.phase, 'required');
});

test('a successful-looking logout redirect cannot hide a live session or an unverifiable auth status', async () => {
  for (const verification of [authenticated(), response(200, { required: true }), response(200, { required: false, authenticated: false }), response(503)]) {
    let authChecks = 0;
    const session = createOfficeSession(async (path) => {
      if (path === '/api/auth') return authChecks++ === 0 ? authenticated() : verification;
      return { status: 200, ok: true, redirected: true };
    });
    await session.check('ai-office.daslab.co.kr');
    await assert.rejects(session.logout(), /로그아웃을 확인하지 못했습니다/);
    assert.equal(session.state.phase, 'error');
    assert.equal(hasOfficeAccess(session.state), false);
  }
});

test('failed logout does not claim success or leave commands enabled', async () => {
  const session = createOfficeSession(async (path) => path === '/api/auth' ? authenticated() : response(503));
  await session.check('ai-office.daslab.co.kr');
  await assert.rejects(session.logout(), /로그아웃을 확인하지 못했습니다/);
  assert.equal(session.state.phase, 'error');
  await assert.rejects(session.request('/api/office/issues', { method: 'POST' }), SessionUnavailableError);
});

test('reauthentication restores only the same recipient, draft and matching retry identity once', () => {
  const storage = memoryStorage();
  assert.equal(saveLoginDraft(storage, draft, 1000), true);
  assert.deepEqual(takeLoginDraft(storage, 2000), draft);
  assert.equal(takeLoginDraft(storage, 2001), null);
  saveLoginDraft(storage, { ...draft, attempt: { ...draft.attempt, agentId: 'researcher' } }, 3000);
  assert.deepEqual(takeLoginDraft(storage, 3001), { ...draft, attempt: null });
  saveLoginDraft(storage, draft, 4000);
  clearLoginDraft(storage);
  assert.equal(storage.getItem(DRAFT_KEY), undefined);
});

test('expired, malformed and unavailable draft storage fail safely', () => {
  const storage = memoryStorage();
  saveLoginDraft(storage, draft, 1000);
  assert.equal(takeLoginDraft(storage, 1000 + 24 * 60 * 60 * 1000 + 1), null);
  storage.setItem(DRAFT_KEY, '{bad json');
  assert.equal(takeLoginDraft(storage), null);
  saveLoginDraft(storage, { ...draft, instruction: 'x'.repeat(8001) }, 1000);
  assert.equal(takeLoginDraft(storage, 2000), null);
  assert.equal(saveLoginDraft({ setItem() { throw new Error('disabled'); } }, draft), false);
});

test('public navigation keeps relative office links and hides private loopback addresses', () => {
  assert.equal(visibleLink('/office.html', 'ai-office.daslab.co.kr'), '/office.html');
  assert.equal(visibleLink('http://127.0.0.1:8772/', 'ai-office.daslab.co.kr'), undefined);
  assert.equal(visibleLink('http://localhost:3100/', 'ai-office.daslab.co.kr'), undefined);
  assert.equal(visibleLink('http://[::1]:3100/', 'ai-office.daslab.co.kr'), undefined);
  assert.equal(visibleLink('http://127.0.0.1:3100/', '127.0.0.1'), 'http://127.0.0.1:3100/');
  assert.equal(visibleLink('javascript:alert(1)', 'ai-office.daslab.co.kr'), undefined);
});
