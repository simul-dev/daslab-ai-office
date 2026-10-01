import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPairingController } from './pairing.mjs';

let qrGenerator: Promise<any> | null = null;
function loadQRGenerator() {
  const browser = window as any;
  if (typeof browser.qrcode === 'function') return Promise.resolve(browser.qrcode);
  if (!qrGenerator) qrGenerator = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = '/qr-code.js';
    script.onload = () => typeof browser.qrcode === 'function' ? resolve(browser.qrcode) : reject(new Error('QR 코드를 준비하지 못했습니다.'));
    script.onerror = () => { script.remove(); reject(new Error('QR 코드를 불러오지 못했습니다.')); };
    document.head.append(script);
  }).catch((error) => { qrGenerator = null; throw error; });
  return qrGenerator;
}

function drawQRCode(container: HTMLDivElement, generator: any, url: string) {
  const qr = generator(0, 'M');
  qr.addData(url); qr.make();
  const count = qr.getModuleCount(), quiet = 4, cell = 6;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = (count + quiet * 2) * cell;
  canvas.setAttribute('aria-hidden', 'true');
  const context = canvas.getContext('2d');
  if (!context) throw new Error('QR 표시를 준비하지 못했습니다. 아래 연결 주소를 복사해주세요.');
  context.fillStyle = '#ffffff'; context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = '#0b1525';
  for (let row = 0; row < count; row += 1) for (let col = 0; col < count; col += 1) {
    if (qr.isDark(row, col)) context.fillRect((col + quiet) * cell, (row + quiet) * cell, cell, cell);
  }
  container.replaceChildren(canvas);
}

export function PhonePairing({ session, publicOrigin }: { session: any; publicOrigin: string }) {
  const [state, setState] = useState<any>({ phase: 'closed' });
  const [renderError, setRenderError] = useState('');
  const [copyStatus, setCopyStatus] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const qrContainer = useRef<HTMLDivElement>(null);
  const pairing = useMemo(() => createPairingController({ request: (path: string, options: any) => session.request(path, options), publicOrigin, onChange: setState }), [session, publicOrigin]);
  const visible = !['closed', 'closing'].includes(state.phase);

  useEffect(() => {
    if (visible && !dialog.current?.open) dialog.current?.showModal();
    if (!visible && dialog.current?.open) dialog.current?.close();
  }, [visible]);
  useEffect(() => {
    let alive = true;
    setRenderError(''); setCopyStatus('');
    qrContainer.current?.replaceChildren();
    if (state.phase === 'ready' && state.url) {
      loadQRGenerator().then((generator) => { if (alive && qrContainer.current) drawQRCode(qrContainer.current, generator, state.url); })
        .catch(() => { if (alive) setRenderError('QR을 불러오지 못했습니다. 아래 연결 주소를 복사해서 휴대폰에서 열어주세요.'); });
    }
    return () => { alive = false; qrContainer.current?.replaceChildren(); };
  }, [state.phase, state.url]);
  useEffect(() => () => { void pairing.close(); }, [pairing]);

  const copy = async () => {
    if (state.phase !== 'ready' || !state.url) return;
    try { await navigator.clipboard.writeText(state.url); setCopyStatus('연결 주소를 복사했습니다. 본인 휴대폰에서 열어주세요.'); }
    catch { setCopyStatus('자동 복사가 지원되지 않습니다. 연결 주소를 선택해 직접 복사해주세요.'); }
  };
  const close = () => { void pairing.close(); };

  return <>
    <button className="phone-button" onClick={() => { void pairing.start(); }} disabled={state.phase === 'closing' || state.phase === 'preparing'}><span aria-hidden="true">▯</span>{state.phase === 'closing' ? '연결 정리 중…' : '휴대폰 연결'}</button>
    <dialog ref={dialog} className="pair-dialog" aria-labelledby="pair-title" onCancel={(event) => { event.preventDefault(); close(); }}>
      <div className="pair-heading"><span className="eyebrow">MY OFFICE, ANYWHERE</span><button className="icon-button" onClick={close} aria-label="휴대폰 연결 닫기">×</button></div>
      <h2 id="pair-title">내 오피스를 휴대폰으로.</h2>
      <p className="pair-intro">휴대폰 카메라로 아래 QR을 스캔하세요.<br />연결하면 같은 팀과 업무가 휴대폰에서도 열립니다.</p>
      <div className={`pair-qr ${state.phase}`} ref={qrContainer} role="img" aria-label="휴대폰 연결 QR 코드" />
      {state.phase === 'preparing' && <p className="pair-status" role="status"><span className="progress-dot" />안전한 연결 코드를 준비하고 있습니다…</p>}
      {state.phase === 'ready' && <>
        {renderError && <p className="pair-error" role="alert">{renderError}</p>}
        <p className="pair-status" role="status">{new Date(state.expiresAt).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}까지 한 번 연결할 수 있습니다.</p>
        <details className="pair-address"><summary>QR을 스캔하기 어려운가요?</summary><label htmlFor="phone-link">연결 주소를 본인 휴대폰에서 열어주세요.</label><div><input id="phone-link" aria-label="일회용 휴대폰 연결 주소" readOnly value={state.url} autoComplete="off" spellCheck={false} onFocus={(event) => event.target.select()} /><button onClick={copy}>주소 복사</button></div>{copyStatus && <p role="status">{copyStatus}</p>}</details>
      </>}
      {state.phase === 'expired' && <div className="pair-feedback"><strong>연결 코드가 만료됐습니다.</strong><p>새 코드를 만들어 다시 스캔해주세요.</p><button className="primary-button" onClick={() => { void pairing.start(); }}>새 QR 코드 만들기 <span>↗</span></button></div>}
      {state.phase === 'error' && <div className="pair-feedback" role="alert"><strong>연결 코드를 준비하지 못했습니다.</strong><p>{state.error || '연결 상태를 확인한 뒤 다시 시도해주세요.'}</p><button className="primary-button" onClick={() => { void pairing.start(); }}>다시 시도 <span>↗</span></button></div>}
      <div className="pair-footnote"><span aria-hidden="true">◇</span><p>본인 기기만 연결해주세요. 연결 뒤 30일 동안 로그인 상태가 유지됩니다. 이 창을 닫으면 사용 전 코드가 취소됩니다.</p></div>
    </dialog>
  </>;
}
