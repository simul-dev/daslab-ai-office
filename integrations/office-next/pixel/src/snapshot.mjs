/** Pure Paperclip-to-view projection. Task assignment is never execution proof. */
export function activityFor(agent, connected = true) {
  if (!connected) return { key: 'unknown', label: '연결 확인 필요', working: false, attention: false };
  const state = String(agent.status || '').toLowerCase();
  const run = String(agent.activeRun?.status || '').toLowerCase();
  if (['running', 'busy', 'working'].includes(state) || run === 'running') {
    return { key: 'working', label: '작업 중', working: true, attention: false };
  }
  if (['paused', 'terminated', 'disabled'].includes(state)) {
    return { key: 'paused', label: state === 'terminated' ? '활동 종료' : '일시 정지', working: false, attention: false };
  }
  if (state === 'pending_approval') return { key: 'paused', label: '등록 승인 대기', working: false, attention: false };
  if (state === 'waiting_on_team' && agent.currentIssue?.waitingOnTeam === true) {
    return { key: 'team_wait', label: '팀 작업 대기', working: false, attention: false };
  }
  if (agent.blockedReason || ['blocked', 'error', 'needs_input', 'needs_approval'].includes(state) || agent.currentIssue?.status === 'blocked') {
    return { key: 'blocked', label: '도움 필요', working: false, attention: true };
  }
  if (agent.currentIssue?.status === 'in_review') return { key: 'review', label: '검토 대기', working: false, attention: false };
  if (run === 'queued' || agent.currentIssue && ['todo', 'backlog', 'in_progress'].includes(agent.currentIssue.status)) {
    return { key: 'assigned', label: '배정됨 · 실행 대기', working: false, attention: false };
  }
  if (['idle', 'active', 'ready'].includes(state)) return { key: 'idle', label: '대기', working: false, attention: false };
  return { key: 'unknown', label: '상태 확인 중', working: false, attention: false };
}

export function validateSnapshot(value) {
  if (!value || typeof value !== 'object' || !Array.isArray(value.agents) || !Array.isArray(value.issues)) throw new Error('직원 상태 응답 형식을 확인할 수 없습니다.');
  if (typeof value.observedAt !== 'string' || !Number.isFinite(Date.parse(value.observedAt))) throw new Error('상태 확인 시각이 없습니다.');
  const ids = new Set();
  for (const agent of value.agents) {
    if (!agent || typeof agent.id !== 'string' || !agent.id || ids.has(agent.id) || typeof agent.name !== 'string') throw new Error('직원 명부를 확인할 수 없습니다.');
    ids.add(agent.id);
  }
  return value;
}

export function numericId(id) {
  let hash = 2166136261;
  for (const char of id) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
  return (hash >>> 0) || 1;
}

/** Select the first real employee once; never retarget a draft on roster changes. */
export function selectionForSnapshot(selectedId, agents) {
  return selectedId === null ? agents[0]?.id ?? null : selectedId;
}

export function assignCharacterIds(agents) {
  const used = new Set();
  return new Map([...agents].sort((a, b) => a.id.localeCompare(b.id)).map((agent) => {
    let id = numericId(agent.id);
    while (used.has(id)) id = (id + 1) >>> 0 || 1;
    used.add(id);
    return [agent.id, id];
  }));
}

export function safeLink(value) {
  if (typeof value !== 'string') return undefined;
  if (value.startsWith('/') && !value.startsWith('//')) return value;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined;
  } catch { return undefined; }
}
