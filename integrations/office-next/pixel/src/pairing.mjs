const START_PATH = '/api/owner/pair/start';
const CANCEL_PATH = '/api/owner/pair/cancel';
const POST = () => ({ method: 'POST', body: '{}' });
const MAX_TIMER_DELAY = 2 ** 31 - 1;
const ERRORS = {
  origin: '휴대폰 연결용 공개 주소를 확인하지 못했습니다.',
  start: '휴대폰 연결을 준비하지 못했습니다. 다시 시도해주세요.',
  response: '휴대폰 연결 주소 또는 유효시간을 확인하지 못했습니다.',
  cancel: '휴대폰 연결 종료를 확인하지 못했습니다. 종료를 다시 시도해주세요.',
};

function canonicalOrigin(value) {
  try {
    const url = new URL(value);
    return typeof value === 'string' && url.protocol === 'https:' && !url.username && !url.password
      && url.origin === value ? value : null;
  } catch { return null; }
}

function validPairing(value, origin, time) {
  if (typeof value?.url !== 'string' || typeof value.expires_at !== 'number') return false;
  const expiresAt = value.expires_at * 1000;
  if (!Number.isFinite(expiresAt) || !Number.isFinite(time) || expiresAt <= time) return false;
  try {
    const url = new URL(value.url);
    return url.protocol === 'https:' && url.origin === origin && !url.username && !url.password
      && url.pathname === '/login' && !url.search && /^#pair=[A-Za-z0-9_-]{43}$/.test(url.hash)
      && value.url === `${origin}/login${url.hash}`;
  } catch { return false; }
}

/**
 * Explicit, memory-only pairing lifecycle. expiresAt is milliseconds since epoch.
 * Methods resolve to state, including errors. Calling start while closing does
 * not queue issuance. After failed cancellation, start/close retry only the
 * cancellation; another explicit start is required to issue a new QR.
 */
export function createPairingController({ request, publicOrigin, onChange = () => {}, now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout }) {
  const origin = canonicalOrigin(publicOrigin);
  let state = Object.freeze({ phase: 'closed' });
  let generation = 0;
  let startPromise = null;
  let rawRequest = null;
  let cancelPromise = null;
  let cancellationRequired = false;
  let afterCancel = { phase: 'closed' };
  let expiryTimer = null;

  const publish = (next) => { state = Object.freeze(next); onChange(state); };
  const clearExpiry = () => {
    if (expiryTimer !== null) clearTimer(expiryTimer);
    expiryTimer = null;
  };

  function cancelTo(phase, error) {
    generation += 1;
    clearExpiry();
    afterCancel = error ? { phase, error } : { phase };
    if (cancelPromise) return cancelPromise;
    if (!cancellationRequired) {
      publish(afterCancel);
      return Promise.resolve(state);
    }

    // A pending start may already have issued a server token. Its response must
    // settle before cancel, so it cannot create a live token after cancellation.
    const pending = rawRequest;
    cancelPromise = (async () => {
      try {
        await pending?.catch(() => {});
        await request(CANCEL_PATH, POST());
        cancellationRequired = false;
        publish(afterCancel);
      } catch {
        publish({ phase: 'error', error: ERRORS.cancel });
      } finally {
        cancelPromise = null;
      }
      return state;
    })();
    // Never keep a URL visible during network cleanup, including on failure.
    publish({ phase: 'closing' });
    return cancelPromise;
  }

  function armExpiry(expiresAt, version) {
    if (version !== generation || state.phase !== 'ready') return;
    const remaining = expiresAt - now();
    if (remaining <= 0 || !Number.isFinite(remaining)) {
      void cancelTo('expired');
      return;
    }
    expiryTimer = setTimer(() => {
      if (version !== generation || state.phase !== 'ready') return;
      expiryTimer = null;
      armExpiry(expiresAt, version);
    }, Math.min(remaining, MAX_TIMER_DELAY));
  }

  function start() {
    if (cancelPromise) return cancelPromise;
    if (startPromise) return startPromise;
    if (state.phase === 'ready') return Promise.resolve(state);
    if (cancellationRequired) return cancelTo('closed');
    if (!origin) {
      publish({ phase: 'error', error: ERRORS.origin });
      return Promise.resolve(state);
    }

    const version = ++generation;
    cancellationRequired = true;
    // Assign both promises before notifying listeners, including synchronous
    // listeners that close the dialog in response to the preparing state.
    rawRequest = Promise.resolve().then(() => request(START_PATH, POST()));
    startPromise = (async () => {
      let value;
      try {
        value = await rawRequest;
      } catch {
        if (version === generation) await cancelTo('error', ERRORS.start);
        return state;
      } finally {
        rawRequest = null;
      }
      if (version !== generation) return state;
      if (!validPairing(value, origin, now())) {
        await cancelTo('error', ERRORS.response);
        return state;
      }
      const expiresAt = value.expires_at * 1000;
      publish({ phase: 'ready', url: value.url, expiresAt });
      armExpiry(expiresAt, version);
      return state;
    })().finally(() => { startPromise = null; });
    publish({ phase: 'preparing' });
    return startPromise;
  }

  return {
    get state() { return state; },
    start,
    close() { return cancelTo('closed'); },
  };
}
