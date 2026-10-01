import test from 'node:test';
import assert from 'node:assert/strict';
import { createPairingController } from '../src/pairing.mjs';

const origin = 'https://office.example.test';
// Deliberately invalid synthetic user-info URLs; no real account credentials.
const withSyntheticUserInfo = (value) => {
  const url = new URL(value);
  url.username = 'fixture-user';
  url.password = 'fixture-password';
  return url.href;
};
// Synthetic test value, never obtained from the live pairing endpoint.
const pairing = (expiresAt = 2000) => ({ url: `${origin}/login#pair=${'a'.repeat(43)}`, expires_at: expiresAt / 1000 });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const flush = async () => { for (let count = 0; count < 8; count += 1) await Promise.resolve(); };
function fixture(handler = async () => pairing(), options = {}) {
  const calls = [];
  const changes = [];
  const timers = new Map();
  let time = 1000;
  let timerId = 0;
  const controller = createPairingController({
    publicOrigin: origin,
    request(path, init) { calls.push({ path, init }); return handler(path, init); },
    onChange(next) { changes.push(next); },
    now: () => time,
    setTimer(callback, delay) {
      const id = ++timerId;
      timers.set(id, { callback() { timers.delete(id); callback(); }, delay });
      return id;
    },
    clearTimer(id) { timers.delete(id); },
    ...options,
  });
  return { controller, calls, changes, timers, setTime(value) { time = value; } };
}

test('construction and closing without explicit start never issue a request', async () => {
  const { controller, calls, timers } = fixture();
  await flush();
  assert.deepEqual(controller.state, { phase: 'closed' });
  await controller.close();
  assert.equal(calls.length, 0);
  assert.equal(timers.size, 0);
});

test('double start shares a pending operation and a ready QR is not reissued', async () => {
  const pending = deferred();
  const { controller, calls, timers } = fixture(() => pending.promise);
  const first = controller.start();
  assert.equal(controller.state.phase, 'preparing');
  assert.equal(controller.start(), first);
  await flush();
  assert.deepEqual(calls, [{ path: '/api/owner/pair/start', init: { method: 'POST', body: '{}' } }]);
  pending.resolve(pairing());
  await first;
  assert.equal(controller.state.phase, 'ready');
  assert.equal(controller.state.url, pairing().url);
  assert.equal(controller.state.expiresAt, 2000);
  assert.equal(Object.isFrozen(controller.state), true);
  assert.equal([...timers.values()][0].delay, 1000);
  await controller.start();
  assert.equal(calls.length, 1);
});

test('close during issuance hides the QR immediately and cancels only after issuance settles', async () => {
  const issuing = deferred();
  const cancelling = deferred();
  const { controller, calls, changes, timers } = fixture((path) => path.endsWith('/start') ? issuing.promise : cancelling.promise);
  const start = controller.start();
  await flush();
  const close = controller.close();
  assert.deepEqual(controller.state, { phase: 'closing' });
  assert.equal(controller.start(), close);
  assert.equal(controller.close(), close);
  await flush();
  assert.equal(calls.length, 1);
  issuing.resolve(pairing());
  await start;
  await flush();
  assert.deepEqual(calls.map(({ path }) => path), ['/api/owner/pair/start', '/api/owner/pair/cancel']);
  assert.deepEqual(calls[1].init, { method: 'POST', body: '{}' });
  assert.equal(changes.some((value) => value.url), false);
  assert.equal(controller.state.phase, 'closing');
  assert.equal(timers.size, 0);
  cancelling.resolve({ ok: true });
  await close;
  assert.deepEqual(controller.state, { phase: 'closed' });
  assert.equal(calls.length, 2);
});

test('closing ready state clears URL and timer before cancellation completes', async () => {
  const pending = deferred();
  const { controller, timers } = fixture((path) => path.endsWith('/start') ? pairing() : pending.promise);
  await controller.start();
  const close = controller.close();
  assert.deepEqual(controller.state, { phase: 'closing' });
  assert.equal(timers.size, 0);
  pending.resolve({ ok: true });
  await close;
  assert.deepEqual(controller.state, { phase: 'closed' });
});

test('failed cancellation stays closed and explicit retry only cancels before a fresh start', async () => {
  let cancelAttempts = 0;
  const retry = deferred();
  const { controller, calls, timers } = fixture((path) => {
    if (path.endsWith('/start')) return pairing();
    cancelAttempts += 1;
    if (cancelAttempts === 1) throw new Error(pairing().url);
    return retry.promise;
  });
  await controller.start();
  await controller.close();
  assert.equal(controller.state.phase, 'error');
  assert.equal(controller.state.url, undefined);
  assert.equal(controller.state.error.includes(pairing().url), false);
  assert.equal(timers.size, 0);
  const retrying = controller.start();
  assert.equal(controller.start(), retrying);
  await flush();
  assert.equal(calls.filter(({ path }) => path.endsWith('/start')).length, 1);
  retry.resolve({ ok: true });
  await retrying;
  assert.deepEqual(controller.state, { phase: 'closed' });
  assert.equal(calls.filter(({ path }) => path.endsWith('/start')).length, 1);
  await controller.start();
  assert.equal(controller.state.phase, 'ready');
  assert.equal(calls.filter(({ path }) => path.endsWith('/start')).length, 2);
});

test('expiry clears the URL while revoking and finishes expired without reissuing', async () => {
  const pending = deferred();
  const { controller, timers, calls, setTime } = fixture((path) => path.endsWith('/start') ? pairing() : pending.promise);
  await controller.start();
  const timer = [...timers.values()][0];
  setTime(2000);
  timer.callback();
  assert.deepEqual(controller.state, { phase: 'closing' });
  const waiting = controller.start();
  await flush();
  assert.equal(calls.length, 2);
  pending.resolve({ ok: true });
  await waiting;
  assert.deepEqual(controller.state, { phase: 'expired' });
  assert.equal(calls.filter(({ path }) => path.endsWith('/start')).length, 1);
});

test('stale timers cannot clear a new QR and early timers wait for actual expiry', async () => {
  const { controller, timers, setTime } = fixture();
  await controller.start();
  const staleTimer = [...timers.values()][0];
  setTime(1500);
  staleTimer.callback();
  assert.equal(controller.state.phase, 'ready');
  assert.equal([...timers.values()].at(-1).delay, 500);
  await controller.close();
  await controller.start();
  staleTimer.callback();
  assert.equal(controller.state.phase, 'ready');
  await controller.close();
  assert.equal(timers.size, 0);
});

test('expiry cancellation failure keeps the QR hidden until explicit cleanup retry succeeds', async () => {
  let cancelAttempts = 0;
  const { controller, timers, calls, setTime } = fixture((path) => {
    if (path.endsWith('/start')) return pairing();
    if (++cancelAttempts === 1) throw new Error('connection lost');
    return { ok: true };
  });
  await controller.start();
  setTime(2000);
  [...timers.values()][0].callback();
  await flush();
  assert.equal(controller.state.phase, 'error');
  assert.equal(controller.state.url, undefined);
  assert.equal(timers.size, 0);
  await controller.start();
  assert.deepEqual(controller.state, { phase: 'closed' });
  assert.equal(calls.filter(({ path }) => path.endsWith('/start')).length, 1);
});

test('a later close takes precedence over expiry while cancellation is pending', async () => {
  const pending = deferred();
  const { controller, timers, setTime } = fixture((path) => path.endsWith('/start') ? pairing() : pending.promise);
  await controller.start();
  setTime(2000);
  [...timers.values()][0].callback();
  const close = controller.close();
  pending.resolve({ ok: true });
  await close;
  assert.deepEqual(controller.state, { phase: 'closed' });
});

test('failed issuance is cleaned up because the server may have issued before the network failed', async () => {
  const { controller, calls } = fixture((path) => {
    if (path.endsWith('/start')) throw new Error(pairing().url);
    return { ok: true };
  });
  await controller.start();
  assert.equal(controller.state.phase, 'error');
  assert.equal(controller.state.url, undefined);
  assert.equal(controller.state.error.includes(pairing().url), false);
  assert.deepEqual(calls.map(({ path }) => path), ['/api/owner/pair/start', '/api/owner/pair/cancel']);
});

test('invalid public origins never reach start or cancel endpoints', async () => {
  for (const publicOrigin of [undefined, '', 'http://office.example.test', `${origin}/`, `${origin}/login`, `${origin}?q=1`, `${origin}#x`, withSyntheticUserInfo(origin), 'https://OFFICE.example.test', ' https://office.example.test', `${origin}:443`]) {
    const { controller, calls } = fixture(undefined, { publicOrigin });
    await controller.start();
    assert.equal(controller.state.phase, 'error');
    await controller.close();
    assert.equal(calls.length, 0);
  }
});

test('untrusted and noncanonical pairing URLs are never exposed and are revoked', async () => {
  const valid = pairing().url;
  const invalidUrls = [
    valid.replace('https:', 'http:'), valid.replace('office.example.test', 'evil.example.test'),
    valid.replace('office.example.test', 'office.example.test.evil.test'),
    withSyntheticUserInfo(valid), valid.replace('/login#', '/login?x=1#'),
    valid.replace('/login#', '/login?#'), valid.replace('/login#', '/other#'),
    valid.replace('/login#', '/path/../login#'), valid.replace('/login#', '/%6cogin#'),
    valid.replace('/login#', '/login/#'), valid.replace('#pair=', '#other='),
    valid.slice(0, -1), `${valid}a`, `${valid}&extra=1`, valid.replace('#pair=a', '#pair=%61'),
    valid.replace('office.example.test', 'OFFICE.example.test'), ` ${valid}`, `${valid}\n`,
    valid.replace('office.example.test', 'office.example.test:443'), '/login#pair=' + 'a'.repeat(43),
  ];
  for (const url of invalidUrls) {
    const { controller, calls, changes } = fixture((path) => path.endsWith('/start') ? { ...pairing(), url } : { ok: true });
    await controller.start();
    assert.equal(controller.state.phase, 'error');
    assert.equal(changes.some((value) => value.url), false);
    assert.equal(calls.at(-1).path, '/api/owner/pair/cancel');
  }
});

test('missing, nonnumeric, nonfinite, and expired expirations never expose a QR', async () => {
  for (const expires_at of [undefined, null, '2', NaN, Infinity, Number.MAX_VALUE, -1, 0, 1]) {
    const { controller, changes } = fixture((path) => path.endsWith('/start') ? { ...pairing(), expires_at } : { ok: true });
    await controller.start();
    assert.equal(controller.state.phase, 'error');
    assert.equal(changes.some((value) => value.url), false);
  }
});

test('synchronous listener close during preparation cannot display a late QR', async () => {
  let controller;
  const changes = [];
  ({ controller } = fixture(undefined, { onChange(next) {
    changes.push(next);
    if (next.phase === 'preparing') void controller.close();
  } }));
  await controller.start();
  await flush();
  assert.deepEqual(controller.state, { phase: 'closed' });
  assert.equal(changes.some((value) => value.url), false);
});
