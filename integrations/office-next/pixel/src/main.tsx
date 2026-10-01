import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { OfficeCanvas } from '@pixel/office/components/OfficeCanvas.js';
import { OfficeState } from '@pixel/office/engine/officeState.js';
import { EditorState } from '@pixel/office/editor/editorState.js';
import { setCharacterTemplates } from '@pixel/office/sprites/spriteData.js';
import { setFloorSprites } from '@pixel/office/floorTiles.js';
import { setWallSprites } from '@pixel/office/wallTiles.js';
import { setCarpetSprites } from '@pixel/office/sprites/carpetTiles.js';
import { buildDynamicCatalog } from '@pixel/office/layout/furnitureCatalog.js';
import { setProviderCapabilities } from '@pixel/office/toolUtils.js';
import { overlayProjection } from '@pixel/office/projection.js';
import { activityFor, assignCharacterIds, selectionForSnapshot, validateSnapshot } from './snapshot.mjs';
import { fitOfficeViewport } from './viewport.mjs';
import { clearLoginDraft, createOfficeSession, hasOfficeAccess, LOGIN_PATH, saveLoginDraft, SessionUnavailableError, takeLoginDraft, visibleLink } from './auth.mjs';
import { PhonePairing } from './PhonePairing';
import './style.css';

type Issue = { id: string; title: string; status: string; description?: string; identifier?: string; assigneeAgentId?: string; url?: string; waitingOnTeam?: boolean; blockedByIssueIds?: string[] };
type Agent = { id: string; name: string; role?: string; status?: string; managerId?: string; currentIssue?: Issue; blockedReason?: string; waitingReason?: string; lastResult?: string | { summary?: string; url?: string }; activeRun?: { id: string; status: string } };
type Snapshot = { observedAt: string; company?: { id: string; name: string }; agents: Agent[]; issues: Issue[]; actionsAvailable?: boolean; capabilities?: { actionsAvailable?: boolean; demoAvailable?: boolean }; source?: string; warning?: string; notice?: string; paperclipUrl?: string; legacyUrl?: string };
const noop = () => {};
const roleNames: Record<string, string> = { ceo: '대표 비서', cto: '기술 책임자', engineer: '개발 담당', researcher: '조사 담당', designer: '디자인 담당', qa: '검수 담당', pm: '프로젝트 매니저', general: '전문 담당', marketer: '마케팅 담당' };
const issueNames: Record<string, string> = { backlog: '예정', todo: '배정 대기', in_progress: '진행', in_review: '검토 대기', done: '완료', blocked: '막힘', cancelled: '취소' };
const issueLabel = (issue: Issue) => issue.waitingOnTeam ? '팀 작업 대기' : issueNames[issue.status] || issue.status;
const roleLabel = (agent: Agent) => roleNames[agent.role || ''] || agent.role || '전문 담당';
const roleSubtitle = (agent: Agent) => roleLabel(agent).trim() === agent.name.trim() ? null : roleLabel(agent);

async function loadOffice() {
  const response = await fetch('./pixel-assets.json', { cache: 'force-cache' });
  if (!response.ok) throw new Error('사무실 그래픽을 불러오지 못했습니다.');
  const assets = await response.json();
  setCharacterTemplates(assets.characters);
  setFloorSprites(assets.floors);
  setWallSprites(assets.walls);
  setCarpetSprites(assets.carpets);
  buildDynamicCatalog(assets.furniture);
  setProviderCapabilities({ readingTools: ['Read', 'Research'], subagentToolNames: [] });
  return new OfficeState(assets.layout);
}

function App() {
  const [restoredDraft] = useState(() => { try { return takeLoginDraft(window.sessionStorage); } catch { return null; } });
  const [auth, setAuth] = useState<{ phase: string; message?: string; pairingAvailable?: boolean; publicOrigin?: string }>({ phase: 'checking' });
  const [authAttempt, setAuthAttempt] = useState(0);
  const [authMessage, setAuthMessage] = useState('');
  const [office, setOffice] = useState<OfficeState | null>(null);
  const [assetError, setAssetError] = useState('');
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [connection, setConnection] = useState<'loading' | 'connected' | 'error'>('loading');
  const [connectionError, setConnectionError] = useState('');
  const [selected, setSelected] = useState<string | null>(restoredDraft?.agentId ?? null);
  const [mode, setMode] = useState<'office' | 'list'>('office');
  const [zoom, setZoom] = useState(2);
  const [command, setCommand] = useState(restoredDraft?.instruction || '');
  const [submitState, setSubmitState] = useState<'idle' | 'sending'>('idle');
  const [submitMessage, setSubmitMessage] = useState(restoredDraft?.instruction ? '작성 중인 지시를 복원했습니다. 받는 직원을 확인한 뒤 직접 전송해주세요.' : '');
  const [selectedIssue, setSelectedIssue] = useState<string | null>(null);
  const [issueDetails, setIssueDetails] = useState<any>(null);
  const [issueError, setIssueError] = useState('');
  const commandAttempt = useRef<{ agentId: string; instruction: string; requestId: string } | null>(restoredDraft?.attempt || null);
  const submitting = useRef(false);
  const editor = useMemo(() => new EditorState(), []);
  const pan = useRef({ x: 0, y: 0 });
  const canvasContainer = useRef<HTMLDivElement>(null);
  const refresh = useRef<() => void>(noop);
  const session = useMemo(() => createOfficeSession(fetch, (next: { phase: string; message?: string }) => {
    setAuth(next);
    if (!hasOfficeAccess(next)) {
      setSnapshot(null); setIssueDetails(null); setSelectedIssue(null);
      setConnection('loading'); setConnectionError('');
    }
  }), []);
  const accessAllowed = hasOfficeAccess(auth);
  const connected = accessAllowed && connection === 'connected';
  const agents = snapshot?.agents || [];
  const characterIds = useMemo(() => assignCharacterIds(agents), [agents]);
  const current = agents.find((a) => a.id === selected);
  const actionsAvailable = accessAllowed && (snapshot?.actionsAvailable === true || snapshot?.capabilities?.actionsAvailable === true)
    && !!current && !['paused', 'terminated', 'disabled', 'pending_approval'].includes(current.status || '');

  useEffect(() => { let alive = true; loadOffice().then((next) => alive && setOffice(next)).catch((err) => alive && setAssetError(err.message)); return () => { alive = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => { controller.abort(); setAuth({ phase: 'error', message: '접속 권한 확인이 지연되고 있습니다. 다시 확인해주세요.' }); }, 9000);
    void session.check(window.location.hostname, controller.signal).finally(() => clearTimeout(timeout));
    return () => { controller.abort(); clearTimeout(timeout); };
  }, [session, authAttempt]);
  useEffect(() => {
    if (!accessAllowed) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let request: AbortController | null = null;
    let inFlight = false;
    const poll = async () => {
      if (stopped || inFlight) return;
      inFlight = true;
      clearTimeout(timer);
      request = new AbortController();
      const timeout = setTimeout(() => request?.abort(), 9000);
      try {
        const next = validateSnapshot(await session.request('/api/office/snapshot', { signal: request.signal })) as Snapshot;
        if (!stopped) { setSnapshot(next); setSelected((id) => selectionForSnapshot(id, next.agents)); setConnection('connected'); setConnectionError(''); }
      } catch (err) {
        if (!stopped && !(err instanceof SessionUnavailableError)) { setConnection('error'); setConnectionError(err instanceof Error && err.name !== 'AbortError' ? err.message : '업무 서버가 응답하지 않습니다.'); }
      } finally {
        clearTimeout(timeout);
        inFlight = false;
        if (!stopped) timer = setTimeout(poll, 3500);
      }
    };
    refresh.current = poll;
    void poll();
    return () => { stopped = true; clearTimeout(timer); request?.abort(); refresh.current = noop; };
  }, [accessAllowed, session]);

  useEffect(() => {
    if (!office) return;
    const live = new Set<number>(characterIds.values());
    for (const id of office.characters.keys()) if (!live.has(id)) office.removeAgent(id);
    for (const agent of agents) {
      const id = characterIds.get(agent.id)!;
      const state = activityFor(agent, connected);
      if (!office.characters.has(id)) office.addAgent(id, id % 6, 0, undefined, true, agent.name);
      const character = office.characters.get(id)!;
      // Avoid resetting paths every poll; only state transitions change motion.
      if (character.isActive !== state.working) office.setAgentActive(id, state.working);
      office.setAgentTool(id, state.working ? 'Work' : null);
      if (state.attention) office.showPermissionBubble(id); else office.clearPermissionBubble(id);
    }
    office.selectedAgentId = current ? characterIds.get(current.id) || null : null;
  }, [office, agents, characterIds, connected, current]);

  useEffect(() => {
    const container = canvasContainer.current;
    if (!container || !office) return;
    const fit = () => {
      const size = container.getBoundingClientRect();
      const fitted = fitOfficeViewport(office.getLayout(), office.furniture, size, window.devicePixelRatio || 1);
      office.cameraFollowId = null;
      pan.current = fitted.pan;
      setZoom(fitted.zoom);
    };
    fit();
    const resize = new ResizeObserver(fit);
    resize.observe(container);
    return () => resize.disconnect();
  }, [office, mode]);

  useEffect(() => {
    if (!selectedIssue || !accessAllowed) return;
    const controller = new AbortController();
    setIssueDetails((previous: any) => previous?.issue?.id === selectedIssue ? previous : null); setIssueError('');
    session.request(`/api/office/issues/${encodeURIComponent(selectedIssue)}`, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setIssueDetails(value); })
      .catch((err) => { if (!controller.signal.aborted && !(err instanceof SessionUnavailableError)) setIssueError(err.message); });
    return () => controller.abort();
  }, [selectedIssue, snapshot?.observedAt, accessAllowed, session]);

  const chooseCharacter = useCallback((id: number) => {
    const agent = agents.find((candidate) => characterIds.get(candidate.id) === id);
    if (agent) { setSelected(agent.id); setSubmitMessage(''); }
  }, [agents, characterIds]);
  const submitCommand = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!actionsAvailable || !connected || !current || !command.trim() || submitting.current) return;
    submitting.current = true;
    setSubmitState('sending'); setSubmitMessage('');
    const instruction = command.trim();
    if (commandAttempt.current?.agentId !== current.id || commandAttempt.current?.instruction !== instruction) {
      commandAttempt.current = { agentId: current.id, instruction, requestId: crypto.randomUUID() };
    }
    try {
      const result = await session.request('/api/office/issues', { method: 'POST', body: JSON.stringify(commandAttempt.current) });
      setSubmitMessage(result.message || `${result.identifier || '새 업무'}를 전달했습니다. 실행 상태는 업무 목록에서 확인할 수 있습니다.`);
      setCommand(''); commandAttempt.current = null; if (result.id) setSelectedIssue(result.id); refresh.current();
    } catch (err) { setSubmitMessage(err instanceof Error ? err.message : '업무를 전달하지 못했습니다.'); }
    finally { submitting.current = false; setSubmitState('idle'); }
  };

  const goToLogin = () => {
    let saved = false;
    try { saved = saveLoginDraft(window.sessionStorage, { agentId: selected, instruction: command, attempt: commandAttempt.current }); } catch { /* Private browser storage can be disabled. */ }
    if (!saved && command) { setAuthMessage('이 브라우저에서 초안을 보관할 수 없습니다. 작성한 지시를 복사한 뒤 로그인해주세요.'); return; }
    window.location.assign(LOGIN_PATH);
  };
  const logout = async () => {
    if (submitting.current || session.state.phase !== 'authenticated') return;
    setAuthMessage('');
    try {
      await session.logout();
      try { clearLoginDraft(window.sessionStorage); } catch { /* Storage may be disabled. */ }
      setCommand(''); commandAttempt.current = null; setSelected(null);
      window.location.assign(LOGIN_PATH);
    } catch (err) { setAuthMessage(err instanceof Error ? err.message : '로그아웃을 확인하지 못했습니다.'); }
  };

  const working = agents.filter((a) => activityFor(a, connected).working).length;
  const blocked = agents.filter((a) => activityFor(a, connected).attention).length;
  const waiting = agents.filter((a) => activityFor(a, connected).key === 'team_wait').length;
  const manager = agents.find((a) => a.id === current?.managerId);
  const lastResult = typeof current?.lastResult === 'string' ? current.lastResult : current?.lastResult?.summary;
  const resultUrl = typeof current?.lastResult === 'object' ? visibleLink(current.lastResult.url, window.location.hostname) : undefined;
  const paperclipUrl = visibleLink(snapshot?.paperclipUrl, window.location.hostname);
  const legacyUrl = visibleLink(snapshot?.legacyUrl, window.location.hostname);
  const status = current ? activityFor(current, connected) : null;
  const companyName = snapshot?.company?.name?.trim() || 'DAS Lab';
  const officeTitle = companyName.includes('사무실') ? companyName : `${companyName} 사무실`;

  return <div className="app-shell">
    <a className="skip-link" href="#workplace">사무실로 바로 가기</a>
    <header className="topbar">
      <a className="brand" href="#" aria-label="DAS Lab AI Office 홈"><img src="/brand/daslab-dark.png" width="340" height="112" alt="DAS Lab" /><span className="product-name">AI OFFICE<small>TEAM WORKSPACE</small></span></a>
      <nav className="header-nav" aria-label="사무실 메뉴"><a href="#workplace">사무실</a><a href="#team">우리 팀</a>{snapshot?.capabilities?.demoAvailable === true && <a href="/demo/supply-chain" target="_blank" rel="noopener noreferrer">직원이 만든 데모 <span>↗</span></a>}</nav>
      <div className="topbar-end"><span className={`connection ${connected ? 'connected' : connection}`}><i />{auth.phase === 'required' ? '로그인 필요' : auth.phase === 'logging-out' ? '로그아웃 중' : connected ? '업무 연결됨' : connection === 'loading' ? '연결 확인 중' : '연결 확인 필요'}</span>{auth.phase === 'local' && auth.pairingAvailable && auth.publicOrigin && <PhonePairing session={session} publicOrigin={auth.publicOrigin} />}{auth.phase === 'authenticated' && <button className="logout-button" onClick={logout} disabled={submitState === 'sending'}>로그아웃</button>}</div>
    </header>

    <main>
      <section className="page-heading"><div className="stats" aria-label="업무 현황"><div className="metric-working"><span><i className="state-dot working" />실행 중</span><b>{connected ? working : '—'}</b></div><div><span><i className="state-dot team_wait" />팀 결과 대기</span><b>{connected ? waiting : '—'}</b></div><div><span><i className="state-dot blocked" />도움 필요</span><b className={blocked ? 'attention-number' : ''}>{connected ? blocked : '—'}</b></div><div><span><i className="state-dot idle" />우리 팀</span><b>{snapshot ? agents.length : '—'}</b></div></div></section>

      {(auth.phase === 'required' || auth.phase === 'error') && <div className="alert auth-alert" role="alert"><div><strong>{auth.phase === 'required' ? '다시 로그인해주세요.' : '접속 권한을 확인하지 못했습니다.'}</strong><p>{auth.phase === 'required' ? '직원 기록 조회와 지시 전송을 멈췄습니다. 작성 중인 지시와 받는 직원은 로그인 후 복원하며, 자동으로 전송하지 않습니다.' : auth.message}</p>{command && <details><summary>작성 중인 지시 보기</summary><textarea aria-label="보관 중인 지시" value={command} readOnly rows={4} /></details>}{authMessage && <p>{authMessage}</p>}</div>{auth.phase === 'required' ? <button onClick={goToLogin}>로그인하기</button> : <button onClick={() => { setAuthMessage(''); setAuthAttempt((value) => value + 1); }}>다시 확인</button>}</div>}
      {accessAllowed && connection === 'error' && <div className="alert" role="alert">{connectionError} 마지막으로 확인한 기록입니다. 연결이 복구될 때까지 작업 애니메이션을 멈춥니다.<button onClick={() => refresh.current()}>다시 확인</button></div>}
      {snapshot?.warning && <div className="notice">{snapshot.warning}</div>}

      <div className="workspace" id="workplace">
        <section className="office-panel">
          <div className="panel-toolbar"><div className="panel-title"><span className="eyebrow">THE OFFICE</span><h2>{officeTitle}</h2></div><div className="view-tabs" aria-label="보기 방식"><button className={mode === 'office' ? 'selected' : ''} onClick={() => setMode('office')} aria-pressed={mode === 'office'}>▦ 사무실</button><button className={mode === 'list' ? 'selected' : ''} onClick={() => setMode('list')} aria-pressed={mode === 'list'}>☷ 업무 목록</button></div></div>
          {mode === 'office' ? <>
            <div className="canvas-container" ref={canvasContainer}>
              {office ? <><OfficeCanvas officeState={office} onClick={chooseCharacter} isEditMode={false} editorState={editor} onEditorTileAction={noop} onEditorEraseAction={noop} onEditorSelectionChange={noop} onDeleteSelected={noop} onRotateSelected={noop} onDragMove={noop} editorTick={0} zoom={zoom} onZoomChange={setZoom} panRef={pan} showAreas={false} activeAreaLabel={null} /><CharacterLabels office={office} agents={agents} ids={characterIds} container={canvasContainer} pan={pan} zoom={zoom} connected={connected} onSelect={setSelected} selected={current?.id} /></> : <div className="loading-office">{assetError || '사무실을 준비하고 있습니다…'}</div>}
              <div className="canvas-controls"><button aria-label="축소" onClick={() => { const next = Math.max(1, zoom - .5); pan.current = { x: pan.current.x * next / zoom, y: pan.current.y * next / zoom }; setZoom(next); }}>−</button><span>{zoom}×</span><button aria-label="확대" onClick={() => { const next = Math.min(10, zoom + .5); pan.current = { x: pan.current.x * next / zoom, y: pan.current.y * next / zoom }; setZoom(next); }}>+</button></div>
            </div>
            <div className="office-note"><span><i className="state-dot working" />작업 중 <i className="state-dot blocked" />도움 필요 <i className="state-dot idle" />대기</span><small>{connected ? '직원을 눌러 업무를 확인하세요.' : '업무 상태를 확인하고 있습니다.'}</small></div>
          </> : <div className="issue-list">
            {!snapshot?.issues.length && <div className="empty-state"><strong>{snapshot ? '아직 등록된 업무가 없습니다.' : '업무를 확인하고 있습니다.'}</strong><p>업무가 생기면 담당자와 진행 상태가 여기에 표시됩니다.</p></div>}
            {snapshot?.issues.map((issue) => <button key={issue.id} className="issue-row" onClick={() => { if (issue.assigneeAgentId) setSelected(issue.assigneeAgentId); setSelectedIssue(issue.id); }}><span className={`issue-status ${issue.waitingOnTeam ? 'team_wait' : issue.status}`}>{issueLabel(issue)}</span><span><small>{issue.identifier || issue.id.slice(0, 8)}</small><strong>{issue.title}</strong></span><span className="issue-owner">{agents.find((a) => a.id === issue.assigneeAgentId)?.name || '미배정'}</span></button>)}
          </div>}
        </section>

        <aside className="detail-panel" aria-label="직원 상세">
          {current ? <><div className="detail-header"><span className={`avatar avatar-${characterIds.get(current.id)! % 6}`}>{current.name.slice(0, 1)}</span><div>{roleSubtitle(current) && <p>{roleSubtitle(current)}</p>}<h2>{current.name}</h2></div><span className={`status-pill ${status!.key}`}>{status!.label}</span></div>
            <div className="detail-body"><dl className="reporting"><dt>보고 대상</dt><dd>{manager?.name || '대표'}</dd></dl>
              <div className="detail-block"><h3>현재 맡은 일</h3>{current.currentIssue ? <><span className="issue-ref">{current.currentIssue.identifier || current.currentIssue.id.slice(0, 8)} · {issueLabel(current.currentIssue)}</span><p className="current-task">{current.currentIssue.title}</p><button className="text-button" onClick={() => setSelectedIssue(current.currentIssue!.id)}>업무와 보고 기록 보기 ↗</button></> : <p className="muted">현재 배정된 업무가 없습니다.</p>}</div>
              {status?.key === 'team_wait' && <div className="team-wait"><strong>팀 작업 대기</strong><p>{current.waitingReason || '명시된 선행 업무의 결과를 기다리고 있습니다.'}</p></div>}
              {status?.attention && <div className="blocker"><strong>도움이 필요합니다</strong><p>{current.blockedReason || '담당 업무 또는 실행에서 막힘이 기록됐습니다. 상세 업무 기록을 확인해주세요.'}</p></div>}
              <div className="detail-block"><h3>최근 결과</h3><p className={lastResult ? '' : 'muted'}>{lastResult || '아직 확인된 결과가 없습니다.'}</p>{resultUrl && <a className="result-link" href={resultUrl} target="_blank" rel="noreferrer">결과물 보기 ↗</a>}</div>
              <form className="command-form" onSubmit={submitCommand}><label htmlFor="command">{current.name}에게 지시</label><textarea id="command" value={command} onChange={(e) => setCommand(e.target.value)} maxLength={8000} placeholder="원하는 결과를 평소 말하듯 적어주세요." disabled={!actionsAvailable || !connected || submitState === 'sending'} rows={4} /><button className="primary-button" type="submit" disabled={!actionsAvailable || !connected || !command.trim() || submitState === 'sending'}>{submitState === 'sending' ? '전달 중…' : '업무 맡기기'}<span>↗</span></button>{!actionsAvailable && <p className="form-help">현재는 업무 상태를 확인하는 단계입니다. 업무 전달 연결을 준비하고 있습니다.</p>}{submitMessage && <p className="submit-message" role="status">{submitMessage}</p>}</form>
            </div></> : <div className="empty-state"><strong>{selected ? '선택한 직원이 현재 명부에 없습니다.' : snapshot ? '등록된 직원이 없습니다.' : '직원을 확인하고 있습니다.'}</strong><p>{selected ? '작성 중인 지시는 다른 직원에게 옮기지 않았습니다. 계속하려면 아래에서 직원을 직접 선택해주세요.' : '직원이 등록되면 여기서 업무와 보고 관계를 볼 수 있습니다.'}</p></div>}
        </aside>
      </div>

      {selectedIssue && <section className="issue-detail" aria-label="업무 보고 기록">
        <div className="section-title"><h2>{issueDetails?.issue?.title || '업무 기록'}</h2><button className="text-button" onClick={() => setSelectedIssue(null)}>닫기 ×</button></div>
        {issueError ? <p role="alert">{issueError}</p> : !issueDetails ? <p className="muted">업무 기록을 확인하고 있습니다.</p> : <>
          <span className="issue-ref">{issueDetails.issue?.identifier || issueDetails.issue?.id?.slice(0, 8)} · {issueLabel(snapshot?.issues.find((issue) => issue.id === selectedIssue) || issueDetails.issue)}</span>
          <h3>보고와 의견</h3>
          {!issueDetails.comments?.length && <p className="muted">아직 등록된 보고가 없습니다.</p>}
          {issueDetails.comments?.map((comment: any, index: number) => <article className="issue-comment" key={comment.id || index}>
            <div><strong>{agents.find((agent) => agent.id === comment.authorAgentId)?.name || '업무 기록'}</strong><time>{comment.createdAt ? new Date(comment.createdAt).toLocaleString('ko-KR') : ''}</time></div>
            <p>{comment.body}</p>
          </article>)}
          <details className="task-instruction" key={selectedIssue}>
            <summary>업무 지시 보기</summary>
            <p className="issue-description">{issueDetails.issue?.description || '등록된 상세 설명이 없습니다.'}</p>
          </details>
        </>}
      </section>}

      <section className="staff-section" id="team"><div className="section-title"><div><p className="eyebrow">THE PEOPLE BEHIND THE WORK</p><h2>함께 일하는 팀 <span>{agents.length}</span></h2></div></div><div className="staff-grid">{agents.map((agent) => { const state = activityFor(agent, connected); return <button key={agent.id} className={`staff-card ${current?.id === agent.id ? 'selected' : ''}`} onClick={() => { setSelected(agent.id); setSubmitMessage(''); }} aria-pressed={current?.id === agent.id}><div className="staff-top"><span className={`avatar small avatar-${characterIds.get(agent.id)! % 6}`}>{agent.name.slice(0, 1)}</span><div><strong>{agent.name}</strong>{roleSubtitle(agent) && <small>{roleSubtitle(agent)}</small>}</div><i className={`state-dot ${state.key}`} /></div><p>{agent.currentIssue?.title || '현재 배정된 업무 없음'}</p><span className={`staff-state ${state.key}`}>{state.label}<span aria-hidden="true">↗</span></span></button>; })}</div></section>
      <footer><span>© DAS Lab · AI Office {paperclipUrl && <>· <a href={paperclipUrl} target="_blank" rel="noreferrer">관리 화면 ↗</a></>} {legacyUrl && <>· <a href={legacyUrl} target="_blank" rel="noreferrer">이전 업무 기록 ↗</a></>}</span><span>{snapshot ? `마지막 확인 ${new Date(snapshot.observedAt).toLocaleTimeString('ko-KR')}` : '실제 업무 서버 연결 대기'} · <a href="./THIRD-PARTY-NOTICES.txt" target="_blank" rel="noreferrer">오픈소스 안내</a></span></footer>
    </main>
  </div>;
}

function CharacterLabels({ office, agents, ids, container, pan, zoom, connected, onSelect, selected }: any) {
  const [, setTick] = useState(0);
  useEffect(() => { const interval = setInterval(() => setTick((n) => n + 1), 100); return () => clearInterval(interval); }, []);
  const el = container.current;
  if (!el) return null;
  const project = overlayProjection(office.getLayout(), el.getBoundingClientRect(), zoom, pan.current, window.devicePixelRatio || 1);
  return <div className="character-labels">{agents.map((agent: Agent) => {
    const character = office.characters.get(ids.get(agent.id));
    if (!character) return null;
    const state = activityFor(agent, connected);
    return <button key={agent.id} onClick={() => onSelect(agent.id)} className={`character-label ${selected === agent.id ? 'selected' : ''}`} style={{ left: project.toScreenX(character.x), top: project.toScreenY(character.y - 24) }} aria-label={`${agent.name}, ${state.label}`}><i className={`state-dot ${state.key}`} />{agent.name}</button>;
  })}</div>;
}

createRoot(document.getElementById('root')!).render(<App />);
