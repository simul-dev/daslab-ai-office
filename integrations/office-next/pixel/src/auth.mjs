import { safeLink } from './snapshot.mjs';

export const LOGIN_PATH = '/login';
export const DRAFT_KEY = 'das-office-next-reauth-draft-v1';
const DRAFT_TTL = 24 * 60 * 60 * 1000;
export const isLoopback = (hostname) => ['localhost', '127.0.0.1', '::1', '[::1]'].includes(String(hostname).toLowerCase());
export const hasOfficeAccess = (state) => ['local', 'authenticated'].includes(state.phase);

export function pairingOrigin(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.origin === value && !url.username && !url.password ? url.origin : undefined;
  } catch { return undefined; }
}

export class SessionUnavailableError extends Error {
  constructor() { super('로그인이 필요합니다. 지시는 자동으로 다시 전송하지 않습니다.'); this.name = 'SessionUnavailableError'; }
}

/** A 401 invalidates all concurrent reads. Reauthentication never retries a command. */
export function createOfficeSession(fetchImpl, onChange = () => {}) {
  let state = { phase: 'checking' };
  let revision = 0;
  const update = (next) => { revision += 1; state = next; onChange(next); };
  return {
    get state() { return state; },
    async check(hostname, signal) {
      update({ phase: 'checking' });
      const version = revision;
      try {
        const response = await fetchImpl('/api/auth', { credentials: 'same-origin', cache: 'no-store', signal });
        let next;
        if (response.status === 404 && isLoopback(hostname)) next = { phase: 'local' };
        else if (response.status === 401) next = { phase: 'required' };
        else {
          if (!response.ok) throw new Error(`접속 권한을 확인하지 못했습니다. (${response.status})`);
          const value = await response.json();
          if (value.required === true && ['pairing', 'github'].includes(value.mode)) {
            next = { phase: value.authenticated === true ? 'authenticated' : 'required' };
          } else if (isLoopback(hostname) && value.mode === 'local' && value.required === false && value.authenticated === true) {
            const publicOrigin = value.pairing_available === true && value.read_only !== true ? pairingOrigin(value.public_origin) : undefined;
            next = { phase: 'local', ...(publicOrigin ? { pairingAvailable: true, publicOrigin } : {}) };
          } else throw new Error('접속 권한 응답을 확인할 수 없습니다.');
        }
        if (version === revision && !signal?.aborted) update(next);
      } catch (error) {
        if (version === revision && !signal?.aborted) update({ phase: 'error', message: error.message || '접속 권한을 확인하지 못했습니다.' });
      }
    },
    async request(path, options = {}) {
      if (!hasOfficeAccess(state)) throw new SessionUnavailableError();
      const version = revision;
      const headers = { ...options.headers };
      if (options.method === 'POST') Object.assign(headers, { 'Content-Type': 'application/json', 'X-Office-Next': '1', 'X-DAS-Office': '1' });
      const response = await fetchImpl(path, { ...options, headers, credentials: 'same-origin', cache: 'no-store' });
      if (response.status === 401) {
        if (version === revision) update({ phase: 'required' });
        throw new SessionUnavailableError();
      }
      if (version !== revision || !hasOfficeAccess(state)) throw new SessionUnavailableError();
      const value = await response.json();
      if (version !== revision || !hasOfficeAccess(state)) throw new SessionUnavailableError();
      if (!response.ok) throw new Error(value.error || `업무 서버 응답 ${response.status}`);
      return value;
    },
    async logout() {
      if (state.phase !== 'authenticated') throw new SessionUnavailableError();
      update({ phase: 'logging-out' });
      try {
        const response = await fetchImpl('/auth/logout', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-DAS-Office': '1' }, body: '{}' });
        if (!response.ok) throw new Error('로그아웃을 확인하지 못했습니다. 접속 상태를 다시 확인해주세요.');
        // The server follows logout with a 303 to its HTML login page. Confirm
        // revocation against the JSON auth endpoint rather than that redirect.
        const verification = await fetchImpl('/api/auth', { credentials: 'same-origin', cache: 'no-store', redirect: 'error' });
        if (!verification.ok) throw new Error('로그아웃을 확인하지 못했습니다. 접속 상태를 다시 확인해주세요.');
        const status = await verification.json();
        if (status.required !== true || status.authenticated !== false) throw new Error('로그아웃을 확인하지 못했습니다. 접속 상태를 다시 확인해주세요.');
        update({ phase: 'required' });
      } catch (error) {
        update({ phase: 'error', message: error.message });
        throw error;
      }
    },
  };
}

/** Keep only an unsubmitted per-tab draft; never restore an automatic action. */
export function saveLoginDraft(storage, draft, now = Date.now()) {
  try { storage.setItem(DRAFT_KEY, JSON.stringify({ version: 1, savedAt: now, ...draft })); return true; }
  catch { return false; }
}

export function clearLoginDraft(storage) {
  try { storage.removeItem(DRAFT_KEY); } catch { /* Storage may be disabled. */ }
}

export function takeLoginDraft(storage, now = Date.now()) {
  try {
    const raw = storage.getItem(DRAFT_KEY);
    clearLoginDraft(storage);
    if (!raw || raw.length > 40000) return null;
    const value = JSON.parse(raw);
    if (value.version !== 1 || !Number.isFinite(value.savedAt) || now - value.savedAt < 0 || now - value.savedAt > DRAFT_TTL
      || !(value.agentId === null || typeof value.agentId === 'string' && value.agentId.length > 0 && value.agentId.length <= 200)
      || typeof value.instruction !== 'string' || value.instruction.length > 8000) return null;
    const attempt = value.attempt;
    const validAttempt = attempt && attempt.agentId === value.agentId && attempt.instruction === value.instruction.trim()
      && typeof attempt.requestId === 'string' && /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/i.test(attempt.requestId);
    return { agentId: value.agentId, instruction: value.instruction, attempt: validAttempt ? { agentId: attempt.agentId, instruction: attempt.instruction, requestId: attempt.requestId } : null };
  } catch { return null; }
}

/** Public users cannot use the operator PC's private administration addresses. */
export function visibleLink(value, hostname) {
  const link = safeLink(value);
  if (!link) return undefined;
  try { if (isLoopback(new URL(link, 'https://relative.invalid').hostname) && !isLoopback(hostname)) return undefined; }
  catch { return undefined; }
  return link;
}
