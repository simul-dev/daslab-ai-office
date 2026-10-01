"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { snapshot: null, receivedAt: Date.now(), selected: "assistant", tab: "work", filter: "all", detail: null, source: null, connected: false, submitting: false, pending: null, detailBusy: false, detailRequest: 0, toastTimer: null, voiceDraft: null, connectionBusy: false };
  const statusNames = { idle: "대기 중", supervising: "감독", queued: "실행 대기", running: "작업 중", working: "작업 중", waiting: "직원 작업 기다리는 중", pausing: "중지 처리 중", paused: "일시 정지", review: "보고서 도착", accepted: "PM 검수 완료", delivered: "반영 처리 완료", completed: "검증 완료", achieved: "달성 보고", partial: "일부 달성 보고", blocked: "진행 막힘", failed: "실행 실패", deferred: "한도 대기", cancelled: "취소됨", unknown: "미측정" };
  const workflowNames = { planning: "PM이 업무 계획 중", working: "담당 직원이 작업 중", checking: "브라우저에서 기본 동작 확인 중", reviewing: "PM이 결과 검수 중", awaiting_input: "필요한 자료 답변 대기", delivering: "검수한 결과 반영 중", done: "처리 완료", blocked: "진행 막힘" };
  const attentionStates = new Set(["blocked", "failed", "paused", "pausing", "deferred"]);
  const activeStates = new Set(["queued", "running", "waiting", "pausing"]);
  const writableStates = new Set(["queued", "running", "waiting", "paused", "review", "blocked", "failed", "deferred"]);
  const glyphs = { assistant: "AS", "das-pm": "PM", "das-rd": "R&D", "das-mkt": "MKT", "das-sales": "BIZ", owner: "나" };
  class BrowserSpeechInput {
    constructor({host = window, view = document, isAllowed = () => false, onState = () => {}, onTranscript = () => {}} = {}) {
      this.host = host;
      this.view = view;
      this.Recognizer = host.SpeechRecognition || host.webkitSpeechRecognition;
      this.available = typeof this.Recognizer === "function";
      this.isAllowed = isAllowed;
      this.onState = onState;
      this.onTranscript = onTranscript;
      this.recognition = null;
      this.timer = null;
      this.run = 0;
      this.accepted = false;
      this._emit("idle", this.available ? "버튼을 누르면 바로 듣습니다." : "이 브라우저는 음성인식을 지원하지 않습니다. 키보드 마이크를 사용해 주세요.");
    }

    _emit(phase, message) {
      this.phase = phase;
      this.onState({available: this.available, active: !!this.recognition, recording: phase === "starting" || phase === "listening",
        processing: phase === "processing", phase, message});
    }

    _finish(message, abort = false) {
      const recognition = this.recognition;
      if (!recognition) return;
      this.recognition = null;
      ++this.run;
      this.host.clearTimeout(this.timer);
      this.timer = null;
      if (abort) { try { recognition.abort(); } catch { /* The session is already closed. */ } }
      this._emit("idle", message);
    }

    start() {
      if (!this.available || this.recognition || !this.isAllowed() || this.view.hidden) return false;
      let recognition;
      try { recognition = new this.Recognizer(); }
      catch { this._emit("idle", "브라우저 음성인식을 시작하지 못했습니다. 키보드 마이크를 사용해 주세요."); return false; }
      this.recognition = recognition;
      const run = ++this.run;
      this.accepted = false;
      recognition.lang = "ko-KR";
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;
      const current = () => this.recognition === recognition && this.run === run && this.isAllowed() && !this.view.hidden;
      recognition.onstart = () => { if (current()) this._emit("listening", "듣고 있습니다. 끝나면 ‘말하기 완료’를 누르세요."); };
      recognition.onresult = (event) => {
        if (!current() || this.accepted) return;
        let text = "";
        for (let index = event.resultIndex || 0; index < (event.results?.length || 0); index++) {
          const result = event.results[index];
          if (result?.isFinal) { text = String(result[0]?.transcript || "").trim(); if (text) break; }
        }
        if (!text) return;
        if (text.length > 2000) { this._finish("인식 결과가 너무 깁니다. 짧게 나눠 말해 주세요.", true); return; }
        this.accepted = true;
        try { if (this.onTranscript({text}) === false) { this._finish("입력 공간이 부족합니다. 기존 내용을 먼저 정리해 주세요.", true); return; } }
        catch { this._finish("인식된 글을 넣지 못했습니다. 키보드로 입력해 주세요.", true); return; }
        this._finish("입력된 글과 받는 직원을 확인한 뒤 맡겨 주세요.", true);
      };
      recognition.onerror = (event) => {
        if (!current()) return;
        const message = {
          "not-allowed": "마이크 권한이 거부됐습니다. 브라우저 설정을 확인하거나 키보드 마이크를 사용해 주세요.",
          "service-not-allowed": "브라우저 음성인식을 사용할 수 없습니다. 키보드 마이크를 사용해 주세요.",
          "audio-capture": "마이크를 사용할 수 없습니다. 키보드 마이크를 사용해 주세요.",
          "no-speech": "말소리가 들리지 않았습니다. 다시 누르거나 키보드 마이크를 사용해 주세요.",
          network: "음성인식 연결이 실패했습니다. 키보드 마이크를 사용해 주세요.",
        }[event?.error] || "음성인식에 실패했습니다. 키보드 마이크를 사용해 주세요.";
        this._finish(message, true);
      };
      recognition.onend = () => { if (current()) this._finish("말소리를 글자로 확인하지 못했습니다. 다시 누르거나 키보드 마이크를 사용해 주세요."); };
      this._emit("starting", "브라우저 음성인식을 시작하고 있습니다. 마이크 권한을 허용해 주세요.");
      this.timer = this.host.setTimeout(() => { if (current()) this._finish("60초가 지나 인식을 중단했습니다. 짧게 나눠 말해 주세요.", true); }, 60000);
      try { recognition.start(); return true; }
      catch { this._finish("브라우저 음성인식을 시작하지 못했습니다. 키보드 마이크를 사용해 주세요.", true); return false; }
    }

    stop() {
      if (!this.recognition || this.phase === "processing") return false;
      this._emit("processing", "말씀을 글자로 바꾸고 있습니다.");
      try { this.recognition.stop(); return true; }
      catch { this._finish("음성인식을 마치지 못했습니다. 키보드 마이크를 사용해 주세요.", true); return false; }
    }

    cancel() { this._finish("음성 입력을 취소했습니다.", true); }
    toggle() { return this.recognition ? this.stop() : this.start(); }
  }
  window.DASBrowserSpeechInput = BrowserSpeechInput;
  let authenticated = false, voiceEnabled = false, readOnly = false, voiceInput = null, voiceState = {}, browserSpeechInput = null, browserSpeechState = {}, routeTimer = null, routeRevision = 0;
  let pairPublicOrigin = null;
  let pairRequest = 0, pairExpiresAt = 0, pairTimer = null, pairStartPromise = null, pairClosing = false;
  let recipientMode = "auto", autoRoute = null;

  function requireLogin({discardDraft = false} = {}) {
    try {
      if (discardDraft) sessionStorage.removeItem("daslab.office.authDraft");
      else if ($("command-input").value.trim()) sessionStorage.setItem("daslab.office.authDraft", JSON.stringify({text: $("command-input").value, recipient: recipientMode, pending: state.pending, savedAt: Date.now()}));
      sessionStorage.removeItem("daslab.office.voiceDraft");
    } catch { /* Storage may be disabled by browser policy. */ }
    authenticated = false;
    browserSpeechInput?.cancel();
    voiceInput?.setEnabled(false);
    state.source?.close();
    $("command-input").value = "";
    location.replace("/login");
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }
  function append(parent, ...children) { for (const child of children) if (child) parent.append(child); return parent; }
  function replace(parent, ...children) { parent.replaceChildren(...children.filter(Boolean)); return parent; }
  function list(value) { return Array.isArray(value) ? value : []; }
  function topMissions() { return state.snapshot.missions.filter((item) => !item.parent_mission_id); }
  function activeWorkflow(mission) { return mission.workflow && !["done", "blocked"].includes(mission.workflow.stage) && !["cancelled", "accepted", "delivered"].includes(mission.status); }
  function employee(id) { return state.snapshot?.employees.find((item) => item.id === id); }
  function employeeName(id) { return employee(id)?.name || "담당 미정"; }
  function statusLabel(status) { return statusNames[status] || "상태 확인 중"; }
  function tone(status) { if (["running", "working", "achieved", "completed"].includes(status)) return "active"; if (attentionStates.has(status) || status === "partial") return "attention"; if (status === "failed") return "danger"; return "neutral"; }
  function badge(status, label) { return node("span", `badge ${tone(status)}`, label || statusLabel(status)); }
  function timeText(value, includeDate = false) {
    if (!value) return "기록 없음";
    const time = new Date(value);
    if (!Number.isFinite(time.getTime())) return "기록 없음";
    return new Intl.DateTimeFormat("ko-KR", includeDate ? { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" }).format(time);
  }
  function duration(seconds) {
    if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return "미집계";
    const count = Math.max(0, Math.round(Number(seconds)));
    if (count < 60) return `${count}초`;
    if (count < 3600) return `${Math.floor(count / 60)}분 ${count % 60}초`;
    return `${Math.floor(count / 3600)}시간 ${Math.floor(count % 3600 / 60)}분`;
  }
  function number(value) { return Number.isFinite(value) ? value : 0; }
  function missionDuration(mission) {
    const latest = state.snapshot?.missions.find((item) => item.id === mission.id);
    const source = latest || mission;
    if (source.elapsed_unknown) return "시간 미확인";
    const extra = latest && ["running", "pausing"].includes(source.status) ? Math.max(0, (Date.now() - state.receivedAt) / 1000) : 0;
    return duration(source.elapsed_seconds === null || source.elapsed_seconds === undefined ? null : source.elapsed_seconds + extra);
  }
  function durationNode(mission) { const element = node("span", "", `실행 ${missionDuration(mission)}`); element.dataset.missionTime = mission.id; return element; }
  function notice(message) { $("page-error").textContent = message; $("page-error").hidden = !message; }
  function toast(message) {
    clearTimeout(state.toastTimer);
    $("toast").textContent = message;
    $("toast").hidden = false;
    state.toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4800);
  }
  function clearPairCode(message = "") {
    clearTimeout(pairTimer);
    pairExpiresAt = 0;
    $("pair-link").value = "";
    $("pair-qr").replaceChildren();
    $("copy-pair").disabled = true;
    $("pair-status").textContent = message;
  }
  function markPairClosing() {
    pairClosing = true;
    $("pair-device").disabled = true;
    $("revoke-devices").disabled = true;
  }
  let qrLoadPromise = null;
  function ensureQRGenerator() {
    if (typeof window.qrcode === "function") return Promise.resolve();
    if (!qrLoadPromise) qrLoadPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/qr-code.js";
      script.onload = () => typeof window.qrcode === "function" ? resolve() : reject(new Error("QR 코드를 만들지 못했습니다."));
      script.onerror = () => reject(new Error("QR 코드를 불러오지 못했습니다."));
      document.head.append(script);
    }).catch((error) => { qrLoadPromise = null; throw error; });
    return qrLoadPromise;
  }
  function drawPairCode(url) {
    if (typeof window.qrcode !== "function") throw new Error("QR 코드를 만들지 못했습니다. 아래 주소를 복사해 주세요.");
    const qr = window.qrcode(0, "M");
    qr.addData(url);
    qr.make();
    const quietZone = 4, cell = 5, count = qr.getModuleCount();
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = (count + quietZone * 2) * cell;
    canvas.setAttribute("aria-hidden", "true");
    const context = canvas.getContext("2d");
    if (!context) throw new Error("QR 코드를 표시하지 못했습니다. 아래 주소를 복사해 주세요.");
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#080d1a";
    for (let row = 0; row < count; row++) for (let col = 0; col < count; col++) {
      if (qr.isDark(row, col)) context.fillRect((col + quietZone) * cell, (row + quietZone) * cell, cell, cell);
    }
    $("pair-qr").replaceChildren(canvas);
  }
  async function startPairing() {
    if (!authenticated || readOnly || $("pair-device").hidden || pairClosing || pairStartPromise) return;
    const serial = ++pairRequest;
    const dialog = $("pair-dialog");
    clearPairCode("연결 코드를 준비합니다.");
    if (!dialog.open) dialog.showModal();
    $("pair-device").disabled = true;
    $("revoke-devices").disabled = true;
    try {
      const issued = request("/api/owner/pair/start", {});
      pairStartPromise = issued;
      const result = await issued;
      if (serial !== pairRequest || !dialog.open) return;
      const link = new URL(result.url);
      if (link.protocol !== "https:" || link.origin !== pairPublicOrigin || link.username || link.password || link.pathname !== "/login" || link.search || !/^#pair=[A-Za-z0-9_-]{43}$/.test(link.hash)) throw new Error("연결 주소를 확인하지 못했습니다.");
      pairExpiresAt = Number(result.expires_at) * 1000;
      if (!Number.isFinite(pairExpiresAt) || pairExpiresAt <= Date.now()) throw new Error("연결 코드의 유효 시간을 확인하지 못했습니다.");
      $("pair-link").value = link.href;
      $("copy-pair").disabled = false;
      try {
        await ensureQRGenerator();
        if (serial !== pairRequest || !dialog.open) return;
        drawPairCode(link.href);
        $("pair-status").textContent = "이 QR 코드는 10분 뒤 만료됩니다.";
      } catch (error) { $("pair-status").textContent = error.message; }
      pairTimer = setTimeout(() => clearPairCode("연결 코드가 만료됐습니다. ‘휴대폰 연결’을 다시 눌러 주세요."), Math.max(0, pairExpiresAt - Date.now()));
    } catch (error) {
      if (serial === pairRequest && dialog.open) clearPairCode(error.message || "연결 코드를 준비하지 못했습니다.");
    } finally { pairStartPromise = null; $("pair-device").disabled = pairClosing; $("revoke-devices").disabled = pairClosing; }
  }
  function setConnection(connected, label) {
    state.connected = connected;
    $("reconnect").className = `connection ${connected ? "connected" : "offline"}`;
    $("connection-label").textContent = label || (connected ? "실시간 연결" : "연결 재시도 중");
  }
  async function request(path, body) {
    const response = await fetch(path, body === undefined ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json", "X-DAS-Office": "1" }, body: JSON.stringify(body) });
    if (response.status === 401) { requireLogin(); throw new Error("다시 로그인해 주세요."); }
    let result;
    try { result = await response.json(); } catch { throw new Error("서버 응답을 읽지 못했습니다. 연결을 확인해 주세요."); }
    if (!response.ok) throw new Error(typeof result.error === "string" ? result.error : result.error?.message || result.message || `요청을 처리하지 못했습니다 (${response.status}).`);
    return result;
  }

  function acceptSnapshot(snapshot) {
    if (!snapshot || !Array.isArray(snapshot.employees) || !Array.isArray(snapshot.missions)) throw new Error("조직 상태 응답을 확인하지 못했습니다.");
    if (state.snapshot && number(snapshot.revision) < number(state.snapshot.revision)) return;
    const changed = !state.snapshot || snapshot.revision !== state.snapshot.revision;
    if (!state.snapshot && snapshot.primary_contact_id) state.selected = snapshot.primary_contact_id;
    state.snapshot = snapshot;
    state.receivedAt = Date.now();
    if (!employee(state.selected)) state.selected = snapshot.employees.find((item) => item.kind === "assistant")?.id || snapshot.employees.find((item) => item.kind !== "owner")?.id;
    notice("");
    if (!changed) return;
    renderMetrics(); renderTree(); renderInspector(); renderMissions(); renderExecution(); renderStanding(); renderEvents(); renderRecipient();
    $("last-update").textContent = `${timeText(new Date().toISOString())} 반영`;
    if (state.detail && $("mission-dialog").open) {
      const updated = snapshot.missions.find((item) => item.id === state.detail.mission.id);
      if (updated) {
        const contentChanged = updated.updated_at !== state.detail.mission.updated_at || updated.status !== state.detail.mission.status || list(state.detail.children).some((child) => {
          const latest = snapshot.missions.find((item) => item.id === child.id);
          return latest && (latest.updated_at !== child.updated_at || latest.status !== child.status);
        });
        state.detail.mission = updated; refreshDetailStatus();
        if (contentChanged && !state.detailBusy) request(`/api/org/missions/${encodeURIComponent(updated.id)}`).then((detail) => {
          if (state.detail?.mission.id === updated.id && $("mission-dialog").open) { state.detail = detail; refreshDetailStatus(); }
        }).catch(() => {});
      }
    }
  }
  async function loadSnapshot() {
    try { acceptSnapshot(await request("/api/org")); }
    catch (error) { setConnection(false, "연결 확인 필요"); notice(`${error.message} 저장된 화면이 있다면 마지막 수신 상태입니다.`); throw error; }
  }
  function connectStream() {
    state.source?.close();
    const source = new EventSource("/api/org/events");
    state.source = source;
    source.addEventListener("auth-required", requireLogin);
    source.addEventListener("open", () => { setConnection(true); });
    source.addEventListener("snapshot", (event) => {
      try { acceptSnapshot(JSON.parse(event.data)); setConnection(true); }
      catch (error) { notice(error.message); }
    });
    source.addEventListener("error", () => { setConnection(false); notice("실시간 연결이 끊어졌습니다. 자동으로 다시 연결하며, 현재 화면은 마지막 수신 상태입니다."); checkAuth().catch(() => {}); });
  }

  function renderMetrics() {
    const snapshot = state.snapshot;
    const staff = snapshot.employees.filter((item) => item.kind !== "owner");
    const running = snapshot.missions.filter((item) => item.status === "running").length;
    const queue = snapshot.missions.filter((item) => item.status === "queued").length;
    $("metric-employees").textContent = staff.length;
    $("metric-employee-note").textContent = snapshot.standing?.enabled ? "PM · 전문 직원 4명 / 비서 확장 대기" : `비서 · PM · 전문 직원 ${staff.length}명`;
    $("metric-running").textContent = running;
    $("metric-running-note").textContent = `실행 대기 ${queue}건 · 누적 개입 ${number(snapshot.metrics?.interventions)}회`;
    $("metric-achieved").textContent = topMissions().filter((item) => item.outcome === "achieved").length;
    $("metric-attention").textContent = topMissions().filter((item) => attentionStates.has(item.status)).length;
    $("organization-count").textContent = `${staff.length}명`;
    $("mission-count").textContent = topMissions().length;
  }
  function chooseEmployee(id, focusComposer = false) {
    if (!employee(id) || employee(id).kind === "owner") return;
    state.selected = id;
    if (focusComposer) { recipientMode = id; ++routeRevision; }
    renderTree(); renderInspector(); renderRecipient();
    if (focusComposer) { $("command-input").scrollIntoView({ behavior: "smooth", block: "center" }); $("command-input").focus({ preventScroll: true }); }
  }
  function renderTree() {
    const staff = state.snapshot.employees;
    const wrapper = node("div", "tree-root");
    const seen = new Set();
    function branch(person) {
      if (seen.has(person.id)) return null;
      seen.add(person.id);
      const item = node("li", "tree-item");
      if (person.kind === "owner") append(item, append(node("div", "org-owner"), node("strong", "", "나 · 대표"), node("span", "", "최종 감독")));
      else {
        const button = node("button", `employee-node node-${person.kind}${state.selected === person.id ? " selected" : ""}`);
        button.type = "button";
        button.dataset.employeeId = person.id;
        button.setAttribute("aria-pressed", String(state.selected === person.id));
        button.setAttribute("aria-label", `${person.name}, ${statusLabel(person.status)}. 업무와 기억 보기`);
        const status = append(node("span", `node-state ${person.status}`), node("i", "status-dot"), node("span", "", statusLabel(person.status)));
        append(button,
          append(node("div", "node-top"), node("span", "node-glyph", glyphs[person.id] || "AI"), status),
          node("div", "node-name", person.name), node("div", "node-role", person.role),
          node("div", "node-work", person.current_mission?.title || (person.reserved ? "여러 PM이 생길 때 역할 확장" : person.standing_duty ? `정기 책임 · ${person.standing_duty}` : "진행 기록에서 상태 확인")));
        button.addEventListener("click", () => chooseEmployee(person.id));
        append(item, button);
      }
      const children = staff.filter((other) => (other.parent_id || other.manager_id) === person.id);
      if (children.length) append(item, append(node("ul", "tree-list"), ...children.map(branch)));
      return item;
    }
    const roots = staff.filter((person) => !(person.parent_id || person.manager_id) || !staff.some((other) => other.id === (person.parent_id || person.manager_id)));
    append(wrapper, append(node("ul", "tree-list"), ...roots.map(branch)));
    replace($("organization-tree"), wrapper);
  }
  function renderRecipient() {
    const staff = state.snapshot.employees.filter((item) => item.kind !== "owner");
    const automatic = node("option", "", `자동 선택${autoRoute && !autoRoute.needs_clarification ? ` · ${employeeName(autoRoute.employee_id)}` : " · 기본 PM"}`); automatic.value = "auto";
    replace($("recipient"), automatic, ...staff.map((item) => { const option = node("option", "", item.name); option.value = item.id; return option; }));
    $("recipient").value = recipientMode;
    $("recipient").disabled = state.submitting;
    updateComposer();
  }
  function updateComposer() {
    const recording = voiceState.recording || voiceState.busy;
    const browserBusy = browserSpeechState.active === true;
    $("command-submit").disabled = readOnly || state.submitting || !state.snapshot || !authenticated || recording || browserBusy;
    $("keyboard-dictation").disabled = readOnly || state.submitting || !state.snapshot || !authenticated;
    $("keyboard-dictation").classList.toggle("primary", browserSpeechState.available === false);
    $("browser-speech-toggle").hidden = browserSpeechState.available === false;
    $("browser-speech-toggle").disabled = readOnly || state.submitting || !state.snapshot || !authenticated || !voiceEnabled || recording || browserSpeechState.processing || browserSpeechState.available !== true;
    $("browser-speech-toggle").textContent = browserSpeechState.recording ? "■ 말하기 완료" : browserSpeechState.processing ? "음성 처리 중…" : "🎙 말로 입력";
    $("browser-speech-toggle").setAttribute("aria-pressed", String(browserBusy));
    $("browser-speech-cancel").hidden = !browserBusy;
    $("voice-toggle").disabled = readOnly || state.submitting || !authenticated || !voiceEnabled || browserBusy || !(voiceState.canStart || voiceState.canStop);
    $("voice-toggle").textContent = voiceState.recording ? "■ 말하기 완료" : voiceState.busy ? "음성 처리 중…" : "◉ 말로 입력";
    $("voice-toggle").setAttribute("aria-pressed", String(!!voiceState.recording));
    $("voice-cancel").hidden = !voiceState.canCancel;
    if (recipientMode !== "auto") $("recipient-hint").textContent = `${employeeName(recipientMode)}에게 보냅니다. 직접 선택한 직원이 우선입니다.`;
    else if (autoRoute?.needs_clarification) $("recipient-hint").textContent = "여러 직원을 부르셨습니다. 받는 직원 한 명을 직접 선택해 주세요.";
    else if (autoRoute) $("recipient-hint").textContent = `${employeeName(autoRoute.employee_id)}에게 보낼 예정입니다.${autoRoute.status === "default" ? " 직원을 부르지 않으면 PM이 받습니다." : ""} 내용을 확인하고 맡겨 주세요.`;
    else $("recipient-hint").textContent = "“PM, 이 일 해줘”처럼 부르면 직원을 자동으로 고릅니다. 내용을 확인하고 맡겨 주세요.";
  }
  async function routeDraft() {
    clearTimeout(routeTimer);
    const revision = ++routeRevision, text = $("command-input").value;
    if (recipientMode !== "auto" || !text.trim()) { autoRoute = null; if (state.snapshot) renderRecipient(); return null; }
    const result = await request("/api/org/route", {text, auto: true});
    if (revision !== routeRevision || text !== $("command-input").value || recipientMode !== "auto") return null;
    autoRoute = result; renderRecipient(); return result;
  }
  async function checkAuth() {
    const status = await request("/api/auth");
    if (!status.authenticated) { requireLogin(); return false; }
    authenticated = true;
    readOnly = status.read_only === true;
    voiceEnabled = !readOnly && status.voice_enabled !== false;
    $("auth-label").textContent = status.mode === "github" ? `@${status.user.login}` : status.mode === "pairing" ? "연결된 기기" : readOnly ? "읽기 전용 미리보기" : "이 PC에서 사용 중";
    $("logout").hidden = status.mode !== "github" && status.mode !== "pairing";
    pairPublicOrigin = null;
    if (typeof status.public_origin === "string") {
      try {
        const configured = new URL(status.public_origin);
        if (configured.protocol === "https:" && configured.origin === status.public_origin && !configured.username && !configured.password) pairPublicOrigin = configured.origin;
      } catch { /* An invalid public origin must keep phone pairing disabled. */ }
    }
    $("pair-device").hidden = status.mode !== "local" || readOnly || status.pairing_available !== true || !pairPublicOrigin;
    $("revoke-devices").hidden = $("pair-device").hidden;
    voiceInput?.setEnabled(voiceEnabled);
    return true;
  }
  function staffMeta(label, value) { return append(node("div", "staff-meta"), node("span", "", label), node("strong", "", value)); }
  function renderInspector() {
    const person = employee(state.selected);
    if (!person) return;
    $("employee-name").textContent = person.name;
    $("employee-avatar").textContent = glyphs[person.id] || "AI";
    $("employee-role").textContent = person.role;
    $("employee-mandate").textContent = person.mandate;
    if (list(person.capabilities).length) $("employee-mandate").textContent += ` · 가능한 작업: ${person.capabilities.join(" · ")}`;
    $("employee-state").className = `badge ${tone(person.status)}`;
    $("employee-state").textContent = statusLabel(person.status);
    $("assign-selected").disabled = false;
    $("employee-panel").setAttribute("aria-labelledby", `tab-${state.tab}`);
    for (const tab of document.querySelectorAll("[data-tab]")) { tab.setAttribute("aria-selected", String(tab.dataset.tab === state.tab)); tab.tabIndex = tab.dataset.tab === state.tab ? 0 : -1; }
    const content = node("div");
    if (state.tab === "work") renderEmployeeWork(content, person);
    if (state.tab === "memory") renderEmployeeMemory(content, person);
    if (state.tab === "performance") renderEmployeePerformance(content, person);
    const panel = $("employee-panel");
    // A live event must not discard an unfinished memory entry or its focus.
    const memoryInput = panel.querySelector("textarea");
    if (state.tab === "memory" && panel.dataset.person === person.id && memoryInput && (memoryInput.value || document.activeElement === memoryInput)) {
      const memoryItems = content.querySelector(".memory-items");
      panel.querySelector(".memory-items")?.replaceWith(memoryItems);
    } else replace(panel, content);
    panel.dataset.person = person.id;
  }
  function renderEmployeeWork(content, person) {
    if (person.standing_duty) append(content, node("p", "staff-note", `정기 책임: ${person.standing_duty}. 업무별 결과는 아래 미션에 남습니다.`));
    if (person.reserved) append(content, node("p", "staff-note", "현재 대표님의 주 창구는 DAS Lab PM입니다. 비서의 기억과 이력은 여러 PM을 조율할 때 이어서 사용합니다."));
    const assigned = state.snapshot.missions.filter((item) => item.employee_id === person.id || item.accountable_id === person.id || (person.kind === "assistant" && item.requested_employee_id === person.id));
    const active = assigned.filter((item) => item.current_work !== false && !item.superseded_by && (activeStates.has(item.status) || attentionStates.has(item.status)));
    const shown = active.length ? active.slice(0, 3) : assigned.slice(0, 1);
    if (shown.length) {
      append(content, node("p", "mini-label", active.length ? `관여 중인 업무 ${active.length}건` : "최근 맡은 업무"));
      for (const mission of shown) {
        const button = node("button", "staff-work"); button.type = "button";
        append(button, mission.superseded_by ? badge("unknown", "이전 결과") : badge(mission.status, mission.status_label), node("h3", "", mission.title), node("p", "", mission.summary || mission.last_activity || "실행 결과가 도착하면 여기에 요약합니다."));
        if (mission.superseded_by) append(button, node("p", "staff-note", "재작업으로 이어진 이전 결과"));
        button.addEventListener("click", () => openMission(mission.id));
        append(content, button);
      }
    } else append(content, append(node("p", "empty-note"), node("strong", "", "아직 맡긴 업무가 없어요."), node("span", "", "일을 맡기면 담당과 실행 상태, 결과가 이 직원에게 연결됩니다.")));
    append(content, staffMeta("보고 대상", employeeName(person.manager_id || person.parent_id)), staffMeta("마지막 활동", timeText(person.last_activity_at, true)));
    append(content, node("p", "staff-note", person.kind === "assistant" ? "요청은 내용에 따라 담당 직원에게 전달됩니다. 현재 담당 선택은 규칙 기반입니다." : person.kind === "pm" ? state.snapshot.standing?.enabled ? "정기 업무 결과를 함께 검토하고 프로젝트별 담당·우선순위·다음 작업을 정리합니다. 대표의 피드백은 다음 검토에 반영합니다." : state.snapshot.execution?.managed_pm_enabled ? "조직 화면 개선 업무를 계획하고 담당 직원에게 맡깁니다. 결과를 검수하고 필요한 수정을 이어서 요청합니다." : "DAS Lab 미션의 책임자로 기록됩니다. 실행은 전문 직원에게 연결되며, 현재 검수는 보고서 구조 확인까지입니다." : "직원별 역할과 저장된 기억을 다음 실행에 전달합니다. 대기 중에는 LLM을 계속 실행하지 않습니다."));
  }
  function memorySource(memory) {
    if (memory.verification === "ai_unverified") return "AI 기록 · 미검증";
    if (memory.verification === "owner_statement" || memory.source === "owner") return "대표가 전달한 기억";
    return "프로젝트 기준 정보";
  }
  function renderEmployeeMemory(content, person) {
    const items = node("div", "memory-items");
    const memories = list(person.memories);
    append(items, node("p", "mini-label", `저장된 기억 ${person.memory_count ?? memories.length}개 · 최근 ${memories.length}개 표시`));
    if (!memories.length) append(items, node("p", "empty-note", "아직 저장된 기억이 없습니다."));
    for (const memory of memories) append(items, append(node("article", "memory-item"), node("p", "", memory.text), node("small", "", `${memorySource(memory)} · ${timeText(memory.created_at, true)}`)));
    append(content, items);
    const form = node("form", "memory-form");
    const label = node("label", "", "다음 업무에도 기억할 내용"); label.htmlFor = "memory-input";
    const input = node("textarea"); input.id = "memory-input"; input.rows = 2; input.maxLength = 3000; input.required = true; input.placeholder = "예: 데모의 산업적 설명력을 시각 효과보다 먼저 확인해 줘.";
    const button = node("button", "button", "기억 저장"); button.type = "submit";
    const error = node("p", "form-error"); error.setAttribute("role", "alert"); error.hidden = true;
    append(form, label, input, button, error);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); const text = input.value.trim(); if (!text) return;
      button.disabled = true; error.hidden = true;
      try { await request(`/api/org/employees/${encodeURIComponent(person.id)}/memory`, { text }); input.value = ""; await loadSnapshot().catch(() => {}); toast(`${person.name}의 기억에 저장했습니다. 다음 실행에 반영됩니다.`); }
      catch (failure) { error.textContent = failure.message; error.hidden = false; }
      finally { button.disabled = false; }
    });
    append(content, form);
  }
  function renderEmployeePerformance(content, person) {
    const metrics = person.metrics || {};
    const tiles = node("div", "performance-value");
    for (const [label, value] of [["제출한 보고서", metrics.reports], ["검증된 성과", metrics.verified_outcomes]]) append(tiles, append(node("div", "performance-tile"), node("strong", "", number(value)), node("span", "", label)));
    append(content, tiles, staffMeta("배정받은 미션", `${number(metrics.assigned)}건`), staffMeta("실제 실행 시간", metrics.execution_time_unknown ? "중단된 시간 미확인" : duration(metrics.execution_seconds)), staffMeta("대표 개입", `${number(metrics.interventions)}회`), node("p", "staff-note", state.snapshot.execution?.managed_pm_enabled ? "보고서 수와 PM 검수 완료를 사업 성과로 세지 않습니다. 고객 반응·매출 같은 실제 성과는 별도 확인이 필요합니다." : "보고서 수와 검증된 성과는 다릅니다. 현재 자동 검수는 형식과 보고 근거의 일관성까지만 확인하며, 고객 반응·매출·실제 품질은 별도 검증이 필요합니다."));
  }

  function progressLabel(mission) {
    if (mission.workflow && (attentionStates.has(mission.status) || ["cancelled", "accepted", "delivered", "completed"].includes(mission.status))) return statusLabel(mission.status);
    if (mission.workflow) return workflowNames[mission.workflow.stage] || "PM이 진행 관리 중";
    return Number.isInteger(mission.progress_percent) && mission.verification === "structural_only" ? `보고 항목 ${mission.progress_percent}%` : "달성률 미측정";
  }
  function renderMissions() {
    const all = topMissions();
    let missions = all;
    if (state.filter === "active") missions = missions.filter((item) => activeStates.has(item.status));
    if (state.filter === "attention") missions = missions.filter((item) => attentionStates.has(item.status));
    if (state.filter === "reports") missions = missions.filter((item) => list(item.report).length || item.release || item.status === "accepted");
    $("mission-summary").textContent = all.length ? `전체 ${all.length}건 · 담당 직원과 보고 내용을 확인하세요.` : "첫 업무를 맡기면 배정과 실행 기록이 여기에서 시작됩니다.";
    if (!missions.length) {
      replace($("mission-list"), append(node("div", "empty-missions"), node("span", "", "↗"), node("strong", "", state.filter === "all" ? "첫 미션을 기다리고 있습니다." : "이 상태의 미션이 없습니다."), node("p", "", state.filter === "all" ? "위에서 평소 말하듯 업무를 맡겨 보세요.\n지시한 내용과 담당자, 결과를 함께 보존합니다." : "다른 상태를 선택하면 저장된 미션을 볼 수 있습니다.")));
      return;
    }
    const rows = missions.map((mission) => {
      const button = node("button", "mission-row"); button.type = "button"; button.dataset.missionId = mission.id;
      append(button, append(node("div", "mission-row-top"), node("h3", "", mission.title), badge(mission.status, mission.status_label)), node("p", "", mission.summary || mission.last_activity || "실행 결과가 도착하면 요약을 표시합니다."), append(node("div", "mission-row-bottom"), node("span", "", employeeName(mission.employee_id)), append(node("span", "mission-row-meta"), node("span", "progress-text", progressLabel(mission)), durationNode(mission), node("span", "", "보기 ↗"))));
      button.addEventListener("click", () => openMission(mission.id));
      return button;
    });
    replace($("mission-list"), ...rows);
  }
  function renderExecution() {
    const execution = state.snapshot.execution || {};
    const panel = $("execution-content");
    const rows = [];
    const resource = (label, value) => append(node("div", "resource-row"), node("span", "", label), node("strong", "", value));
    rows.push(append(node("div", "runtime-title"), node("span", "runtime-icon", "⌘"), node("span", "", execution.provider === "codex" ? "Codex 구독 실행" : "실행기 확인 중")));
    const connection = execution.connection || {};
    rows.push(resource("실행기 연결", connection.available === true ? "기존 구독 로그인 확인" : connection.available === false ? "연결 확인 필요" : "실행할 때 확인"));
    const checkButton = node("button", "button runtime-check", state.connectionBusy ? "연결 확인 중…" : "Codex 연결 확인"); checkButton.type = "button"; checkButton.disabled = state.connectionBusy;
    checkButton.addEventListener("click", async () => {
      state.connectionBusy = true; checkButton.disabled = true; checkButton.textContent = "연결 확인 중…";
      try { const result = await request("/api/org/connection", {}); toast(result.message || (result.available ? "Codex 연결을 확인했습니다." : "Codex 연결을 확인해 주세요.")); await loadSnapshot(); }
      catch (error) { toast(error.message); }
      finally { state.connectionBusy = false; if (state.snapshot) renderExecution(); }
    });
    rows.push(checkButton);
    rows.push(resource("동시 실행", `${number(execution.active_count)} / ${number(execution.concurrency_limit)}개`));
    if (execution.active_employee_id) rows.push(resource("지금 실행하는 직원", employeeName(execution.active_employee_id)));
    const slots = node("div", "slot-bar"); slots.setAttribute("aria-hidden", "true");
    for (let index = 0; index < Math.min(8, number(execution.concurrency_limit)); index++) append(slots, node("span", `slot-cell${index < number(execution.active_count) ? " busy" : ""}`));
    rows.push(slots, resource("대기 중인 미션", `${number(execution.queued_count)}건`));
    rows.push(resource("오늘 실행 / 내부 제한", `${number(execution.daily_used)} / ${execution.daily_limit ?? "미설정"}회`));
    rows.push(resource("공유 구독 잔여량", execution.remaining_quota === null || execution.remaining_quota === undefined ? "미확인" : String(execution.remaining_quota)));
    if (execution.quota_blocked) rows.push(resource("구독 한도", "한도에 도달해 실행 대기"));
    rows.push(resource("유료 API 자동 전환", execution.paid_fallback ? "활성화됨" : "사용 안 함"));
    if (execution.managed_pm_enabled || execution.project_pm_enabled) rows.push(resource("PM 업무 관리", "배정 · 검수 · 재작업 연결됨"));
    if (connection.available === false && connection.message) rows.push(node("p", "execution-capabilities", connection.message));
    rows.push(node("p", "execution-note", "한 번에 한 직원이 실행됩니다. 한도·실패·서버 중단으로 멈춘 일은 상태를 확인한 후 직접 재개할 수 있습니다."));
    rows.push(node("p", "execution-capabilities", execution.research_enabled ? "전문 직원: 공개 웹 조사·출처 기록·콘텐츠와 제안 초안. PM·R&D의 기존 조직 화면 수정 기능도 유지됩니다. 3D 완성도·실제 게시·고객 접촉은 별도 기록으로 확인합니다." : execution.development_enabled ? "PM·R&D: 조직 운영 화면의 작업본 수정·검사·미리보기 가능. 다른 직원은 자료 분석·문서 작성." : "현재 실행 범위: 제공된 자료의 분석·문서·코드 제안."));
    if (execution.prototype_enabled) rows.push(node("p", "execution-capabilities", "R&D: 격리된 공급망 데모 파일 수정 → 모델 기본 검사 → 실제 브라우저 검수 → PM 결과 검토. 현장 데이터 검증과 3D 제작은 후속 과제입니다."));
    if (execution.project_pm_enabled) rows.push(node("p", "execution-capabilities", "PM 프로젝트: 자료·방법 조사 → 직원 배정 → 검수·재작업 → 성과 보고. 필수 자료 질문에 답하면 이어서 진행합니다. 공급망 GIS 데모는 별도 작업본에서 검증합니다."));
    if (execution.managed_pm_enabled) rows.push(node("p", "execution-capabilities", "조직 화면 개선의 작업·브라우저 검사·PM 검수·요청한 반영 경로도 유지됩니다. 세부 결과는 업무 상세에서 확인할 수 있습니다."));
    replace(panel, ...rows);
  }
  function renderStanding() {
    const panel = $("standing-content");
    if (!panel) return;
    const standing = state.snapshot.standing;
    if (!standing?.enabled) { replace(panel, node("p", "empty-note", "정기 업무가 설정되지 않았습니다.")); return; }
    const names = { dispatching: "업무 배정 중", working: "직원 실행 중", paused: "업무 정지", awaiting_pm: "PM 검토 배정 대기", pm_review: "PM 검토 중", completed: "이번 회차 검토 종료", needs_attention: "막힌 업무 확인 필요" };
    const cycle = standing.current_cycle;
    const rows = [node("p", "mini-label", standing.project.name), node("p", "staff-note", `${standing.schedule} · ${standing.paused ? "신규 배정 정지" : "신규 배정 가능"}`)];
    rows.push(node("p", "staff-note", standing.schedule_registration?.enabled ? "예약 연결됨 · 이 PC와 Codex 앱이 켜져 있어야 실행됩니다." : "예약 실행 연결 전 · 지금 실행 버튼으로 시작할 수 있습니다."));
    rows.push(node("p", "staff-note", cycle ? `${cycle.date} · ${names[cycle.stage] || cycle.stage}` : "아직 시작한 회차가 없습니다."));
    for (const duty of standing.duties) {
      const slot = cycle?.duties[duty.id];
      const row = append(node("div", "resource-row"), node("span", "", `${employeeName(duty.employee_id)} · ${duty.name}`));
      if (slot?.mission_id) { const button = node("button", "button", statusLabel(slot.status)); button.type = "button"; button.addEventListener("click", () => openMission(slot.mission_id)); append(row, button); }
      else append(row, node("strong", "", "배정 전"));
      rows.push(row);
    }
    if (cycle?.pm?.mission_id) { const button = node("button", "button", `PM 통합 검토 · ${statusLabel(cycle.pm.status)}`); button.type = "button"; button.addEventListener("click", () => openMission(cycle.pm.mission_id)); rows.push(button); }
    if (standing.reason) rows.push(node("p", "staff-note", standing.reason));
    rows.push(node("p", "staff-note", `현재 실행 단계: ${standing.project.phase}. 3D 완성도·현장 적합성·외부 발행은 별도로 확인합니다.`));
    for (const [label, path, body] of [["정기 업무 지금 실행", "tick", {}], [standing.paused ? "정기 배정 재개" : "정기 배정 정지", "pause", { paused: !standing.paused }]]) {
      const button = node("button", "button standing-action", label); button.type = "button";
      button.disabled = Boolean(state.standingBusy || (path === "tick" && standing.paused));
      button.addEventListener("click", async () => { state.standingBusy = true; renderStanding(); try { const result = await request(`/api/org/standing/${path}`, body); await loadSnapshot(); toast(result.reason || "정기 업무 상태를 반영했습니다."); } catch (error) { toast(error.message); } finally { state.standingBusy = false; renderStanding(); } });
      rows.push(button);
    }
    replace(panel, ...rows);
  }
  function renderEvents() {
    const events = list(state.snapshot.events).slice(0, 6);
    if (!events.length) { replace($("activity-list"), node("p", "empty-note", "업무를 맡기거나 직원의 기억을 저장하면 실제 활동 기록이 쌓입니다.")); return; }
    replace($("activity-list"), ...events.map((event) => {
      const time = node("time", "", timeText(event.created_at, true)); time.dateTime = event.created_at;
      const text = node("span", "activity-text", event.message);
      return append(node("div", "activity-item"), node("span", "activity-dot"), time, text);
    }));
  }

  async function openMission(id) {
    const requestNumber = ++state.detailRequest;
    const title = node("h2", "", "미션을 불러오고 있습니다."); title.id = "detail-title";
    replace($("mission-detail"), append(node("div", "detail-body"), title));
    if (!$("mission-dialog").open) $("mission-dialog").showModal();
    try {
      const result = await request(`/api/org/missions/${encodeURIComponent(id)}`);
      if (requestNumber !== state.detailRequest || !$("mission-dialog").open) return;
      state.detail = result; renderMissionDetail();
    } catch (error) {
      if (requestNumber !== state.detailRequest) return;
      replace($("mission-detail"), append(node("div", "detail-body"), title, node("p", "form-error", error.message)));
      title.textContent = "미션을 불러오지 못했습니다.";
    }
  }
  function section(title, content) { return append(node("section", "detail-section"), node("h3", "", title), node("p", "", content)); }
  function listSection(title, items) { return items.length ? append(node("section", "detail-section"), node("h3", "", title), append(node("ul"), ...items.map((item) => node("li", "", item)))) : null; }
  function renderWorkflow(body, mission, children) {
    const workflow = mission.workflow;
    if (!workflow) return;
    const panel = node("section", "detail-section");
    const business = workflow.kind === "business";
    append(panel, node("h3", "", "PM이 처리하는 순서"), node("p", "", business ? "자료·문제 정의 → 방법 선택 → 조사·실행 배정 → 검수·재작업 → 성과 보고" : "계획 → 직원 작업 → 기본 동작 확인 → PM 검수 → 요청한 반영·커밋·푸시"));
    if (business && workflow.definition) {
      append(panel, section("해결할 문제", workflow.definition.problem), section("선택한 방법", workflow.definition.selected_method));
      append(panel, listSection("성과지표", list(workflow.definition.metrics).map((metric) => `${metric.name}: 기준 ${metric.baseline} / 목표 ${metric.target} / 결과 ${metric.result || "미측정"}`)));
      append(panel, listSection("목표까지 남은 일", list(workflow.definition.remaining)));
    }
    if (business && workflow.stage === "awaiting_input" && mission.status === "blocked") {
      const form = node("form", "detail-intervention");
      append(form, node("h3", "", "PM이 필요한 자료를 요청했습니다"), node("p", "detail-notice", "자료가 없으면 없다고 알려 주세요. 답변하면 PM이 가능한 방법을 다시 판단하고 이어갑니다."));
      for (const question of list(workflow.input_requests)) {
        const id = `project-input-${workflow.input_round}-${question.id}`;
        const label = node("label", "", question.question); label.htmlFor = id;
        const input = node("textarea"); input.id = id; input.dataset.questionId = question.id; input.dataset.projectAnswer = "true"; input.required = true; input.maxLength = 6000; input.rows = 3;
        append(form, label, node("p", "detail-notice", `${question.needed_for} · 대안: ${question.alternatives}`), input);
      }
      const submit = node("button", "button primary", "답변하고 이어서 진행"); submit.type = "submit"; submit.disabled = state.detailBusy;
      append(form, submit);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (state.detailBusy) return;
        const answers = Object.fromEntries([...form.querySelectorAll("textarea[data-project-answer]")].map((input) => [input.dataset.questionId, input.value.trim()]));
        state.detailBusy = true; submit.disabled = true;
        try {
          const result = await request(`/api/org/missions/${encodeURIComponent(mission.id)}/input`, { input_round: workflow.input_round, answers });
          if (state.detail?.mission.id === mission.id) { state.detail = result; renderMissionDetail(); }
          toast("답변을 저장했습니다. PM이 이어서 진행합니다.");
        } catch (error) { toast(error.message); }
        finally { state.detailBusy = false; submit.disabled = false; }
      });
      append(panel, form);
    }
    const current = ["paused", "pausing", "cancelled", "failed", "deferred", "blocked"].includes(mission.status) ? statusLabel(mission.status) : workflowNames[workflow.stage] || "진행 상태 확인 중";
    append(panel, node("p", "", `현재: ${current}${Number.isInteger(workflow.round) && workflow.round > 0 ? ` · 재작업 ${workflow.round}회` : ""}`));
    if (mission.status === "blocked" || workflow.stage === "blocked") append(panel, node("p", "pause-explanation", mission.summary || mission.last_activity || "진행을 막는 이유를 확인하고 있습니다."));
    if (mission.status === "accepted") append(panel, node("p", "detail-notice", "PM이 작업 결과를 검수했습니다. 고객 반응·매출 같은 사업 성과는 별도 확인이 필요합니다."));
    for (const child of list(children)) {
      const button = node("button", "staff-work"); button.type = "button";
      append(button, child.superseded_by ? badge("unknown", "이전 결과") : badge(child.status, child.status_label), node("h3", "", `${employeeName(child.employee_id)} · ${child.title}`), node("p", "", child.summary || child.last_activity || "아직 결과가 도착하지 않았습니다."));
      if (child.superseded_by) append(button, node("p", "detail-notice", `재작업으로 이어진 이전 결과 · 당시 상태: ${child.status_label || statusLabel(child.status)}`));
      button.addEventListener("click", () => openMission(child.id));
      append(panel, button);
    }
    const verification = workflow.browser_verification;
    if (verification) {
      const names = { passed: "통과", failed: "실패", blocked: "확인하지 못함", unavailable: "확인하지 못함", skipped: "실행하지 않음", running: "확인 중", pending: "확인 대기" };
      append(panel, section("브라우저 기본 동작 확인", verification.summary || names[verification.status] || "결과 확인 필요"));
      const checkNames = { organization_render: "직원 화면 표시", images: "이미지 표시", employee_selection: "직원 선택", employee_tabs: "직원 정보 탭", desktop_layout: "PC 화면 배치", mobile_layout: "모바일 화면 배치", mission_detail: "업무 상세 열기·닫기", network_boundary: "읽기 전용 연결", browser_errors: "브라우저 오류" };
      const checks = list(verification.checks).map((item) => {
        if (typeof item === "string") return item;
        if (!item || typeof item.name !== "string") return null;
        return `${checkNames[item.name] || item.name}: ${names[item.status] || "결과 확인 필요"}${item.status === "failed" && typeof item.detail === "string" ? ` · ${item.detail}` : ""}`;
      }).filter(Boolean);
      const errors = list(verification.errors).filter((item) => typeof item === "string");
      if (errors.length) append(panel, listSection("확인 중 발견한 문제", errors));
      if (checks.length) {
        const fold = node("details", "detail-fold");
        append(fold, node("summary", "", `확인한 항목 ${checks.length}개`), listSection("확인 항목", checks));
        append(panel, fold);
      }
    }
    if (list(workflow.history).length) {
      const fold = node("details", "detail-fold");
      append(fold, node("summary", "", "PM의 진행 기록"));
      for (const event of list(workflow.history)) append(fold, append(node("p", "detail-event"), node("time", "", timeText(event.at, true)), node("span", "", event.summary || workflowNames[event.stage] || "진행 기록")));
      append(panel, fold);
    }
    append(body, panel);
  }
  function refreshDetailStatus() {
    if (!state.detail || !$("mission-dialog").open) return;
    // Preserve unfinished intervention text while making newly arrived results visible.
    const previousText = $("intervention-input")?.value || "";
    const previousRecipient = $("reassign-recipient")?.value;
    const answers = [...$("mission-detail").querySelectorAll("textarea[data-project-answer]")].map((input) => [input.id, input.value]);
    const focusedId = document.activeElement?.id;
    renderMissionDetail();
    if ($("intervention-input")) $("intervention-input").value = previousText;
    if (previousRecipient && $("reassign-recipient")) $("reassign-recipient").value = previousRecipient;
    for (const [id, value] of answers) if ($(id)) $(id).value = value;
    if (focusedId && $(focusedId)) $(focusedId).focus({ preventScroll: true });
  }
  function renderMissionDetail() {
    const detail = state.detail;
    const mission = detail.mission;
    const body = node("div", "detail-body");
    const title = node("h2", "", mission.title); title.id = "detail-title";
    append(body, append(node("div", "detail-title-row"), title, badge(mission.status, mission.status_label)));
    append(body, append(node("div", "detail-meta"), node("span", "", `담당 ${employeeName(mission.employee_id)}`), node("span", "", `책임 ${employeeName(mission.accountable_id)}`), durationNode(mission), node("span", "", `개입 ${number(mission.intervention_count)}회`)));
    append(body, node("p", "detail-summary", mission.summary || mission.last_activity || "아직 실행 결과가 없습니다."));
    if (list(mission.next_actions).length) append(body, listSection("다음 단계에서 할 일", mission.next_actions));
    if (mission.project_context) append(body, node("p", "detail-notice", `작업 대상: ${mission.project_context.name}`));
    if (mission.web_search) append(body, node("p", "detail-notice", `실제 웹 검색 완료 ${number(mission.web_search.completed_count)}회 · 출처 내용과 사업 효과의 검수는 별도입니다.`));
    if (mission.prototype?.ready && /^\/prototypes\/[a-f0-9]{32}\/$/.test(mission.prototype.preview_url || "")) {
      const preview = node("a", "button primary", "시뮬레이션 데모 열기 ↗");
      preview.href = mission.prototype.preview_url; preview.target = "_blank"; preview.rel = "noopener";
      append(body, preview, node("p", "detail-notice", mission.prototype.browser_verified ? "모델 기본 보존식·경계조건·실제 화면 동작 통과. 현장 적합성과 3D 품질은 별도입니다." : "데모 작업본 · 모델과 화면 검수 확인 필요"));
      if (list(mission.prototype.browser?.errors).length) append(body, listSection("데모 검사에서 발견한 문제", mission.prototype.browser.errors));
    }
    if (mission.parent_mission_id) {
      const parentButton = node("button", "button", "PM이 관리하는 전체 업무 보기"); parentButton.type = "button";
      parentButton.addEventListener("click", () => openMission(mission.parent_mission_id));
      append(body, parentButton, node("p", "detail-notice", "이 작업은 PM이 관리합니다. 중지·재개·추가 지시는 전체 업무에서 할 수 있습니다."));
    }
    renderWorkflow(body, mission, detail.children);
    if (mission.delivery?.ready && /^\/previews\/[a-f0-9]{32}\/$/.test(mission.delivery.preview_url || "")) {
      const preview = node("a", "button primary", "디자인 미리보기 열기 ↗");
      preview.href = mission.delivery.preview_url; preview.target = "_blank"; preview.rel = "noopener";
      append(body, preview, node("p", "detail-notice", `변경 파일 ${list(mission.delivery.changed_files).length}개 · 정적 검사 통과 · ${mission.release?.applied_to_live ? "운영 화면 반영 기록 있음" : mission.execution_mode === "preview_import" && mission.delivery.applied_to_live ? "현재 운영 화면과 동일한 복구본" : "운영본 미적용"}. ${mission.workflow?.browser_verification ? "브라우저 기본 동작 확인 결과는 위에 표시됩니다." : "화면·동작 검증은 별도입니다."}`));
      if (!activeStates.has(mission.status) && !activeWorkflow(mission) && !(mission.workflow && mission.status === "cancelled") && !mission.parent_mission_id && mission.delivery.attempt_id) {
        const deliveryActions = node("div", "detail-actions");
        for (const [operation, label] of [["apply", "이 미리보기 반영"], ["commit", "반영하고 커밋"], ["push", "반영·커밋·푸시"]]) {
          const button = node("button", "button", label); button.type = "button"; button.disabled = state.detailBusy;
          button.addEventListener("click", () => submitDelivery(mission.delivery.attempt_id, operation));
          append(deliveryActions, button);
        }
        append(body, deliveryActions);
      }
    }
    if (mission.release) {
      const receipt = mission.release;
      const commitLabel = receipt.commit ? `커밋 ${receipt.commit.slice(0, 12)}` : receipt.already_synced && receipt.verified_commit ? `기존 커밋 ${receipt.verified_commit.slice(0, 12)}` : "커밋 미완료";
      const remoteLabel = receipt.already_synced && receipt.remote_verified ? "이미 원격에 저장된 동일본 확인 · 추가 푸시 없음" : receipt.pushed ? "원격 저장 확인됨" : "푸시 미완료";
      append(body, section("반영 기록", [receipt.applied_to_live ? "운영 파일 반영됨" : "운영 파일 미반영", commitLabel, remoteLabel, mission.workflow?.browser_verification ? "브라우저 확인 결과는 위에 표시" : "브라우저 화면 검수는 별도"].join(" · ")));
    }
    if (mission.status === "pausing") append(body, node("p", "pause-explanation", "실행기를 멈추고 있습니다. 실제 종료가 확인되면 일시 정지 또는 취소 상태로 바뀝니다."));
    append(body, append(node("p", "mission-progress"), node("span", "", `${progressLabel(mission)}${mission.outcome !== "unknown" ? ` · ${statusLabel(mission.outcome)}` : ""}`)));
    if (mission.verification === "structural_only") append(body, node("p", "detail-notice", "AI가 작성한 결과 보고입니다. 구조와 근거 항목의 일관성을 확인했으며, 실제 목표 달성이나 사업 성과가 독립 검증된 것은 아닙니다."));
    const actions = node("div", "detail-actions");
    function actionButton(label, action, className = "") {
      if (mission.parent_mission_id) return;
      const button = node("button", `button ${className}`, label); button.type = "button"; button.disabled = state.detailBusy || (mission.execution_mode === "delivery" && mission.status === "running"); button.addEventListener("click", () => performAction(action)); append(actions, button);
    }
    if (["queued", "running", "waiting", "deferred"].includes(mission.status)) actionButton("일시 정지", "pause");
    if (["paused", "failed", "blocked", "deferred"].includes(mission.status) && (mission.workflow?.stage !== "awaiting_input" || mission.status === "paused")) actionButton("이어서 실행", "resume", "primary");
    if (mission.status === "review" && mission.execution_mode !== "preview_import") actionButton("결과를 바탕으로 다시 실행", "resume");
    if (["queued", "running", "waiting", "paused", "blocked", "failed", "deferred"].includes(mission.status)) actionButton("업무 취소", "cancel", "danger");
    append(body, actions);
    const actionError = node("p", "form-error"); actionError.id = "action-error"; actionError.hidden = true; actionError.setAttribute("role", "alert"); append(body, actionError);
    for (const item of list(mission.report)) append(body, section(item.title, item.content));
    append(body, listSection("완료한 일", list(mission.accomplishments)), listSection("남은 일", list(mission.remaining)));
    if (writableStates.has(mission.status) && !["delivery", "preview_import"].includes(mission.execution_mode) && !mission.parent_mission_id) renderIntervention(body, mission);
    const instructionFold = node("details", "detail-fold");
    append(instructionFold, node("summary", "", "원래 지시와 배정 이유"), section("대표의 지시", mission.text), section("배정 이유", mission.routing || "배정 기록 없음"));
    const priorityNames = { high: "높음", normal: "보통", low: "낮음" };
    const complexityNames = { simple: "간단", standard: "보통", moderate: "보통", complex: "복잡" };
    append(instructionFold, section("처리 판단", `우선순위 ${priorityNames[mission.priority] || "미정"} · 난이도 ${complexityNames[mission.complexity] || "미정"}`));
    for (const instruction of list(mission.instructions)) append(instructionFold, section(`추가 지시 · ${timeText(instruction.created_at, true)}`, instruction.text));
    append(body, instructionFold);
    if (list(mission.limitations).length || mission.progress_basis) {
      const evidence = node("details", "detail-fold"); append(evidence, node("summary", "", "달성 판단의 근거와 한계"), mission.progress_basis ? section("진행률 근거", mission.progress_basis) : null, listSection("확인되지 않은 부분", list(mission.limitations))); append(body, evidence);
    }
    const history = node("details", "detail-fold"); append(history, node("summary", "", `실행·개입 기록 (${number(mission.attempts_count)}회 실행)`));
    for (const attempt of list(detail.attempts)) append(history, section(`${attempt.number}차 실행 · ${employeeName(attempt.employee_id)}`, `${timeText(attempt.started_at, true)} · ${duration(attempt.duration_seconds)}${attempt.error ? `\n${attempt.error}` : ""}`));
    for (const event of list(detail.events)) append(history, append(node("p", "detail-event"), node("time", "", timeText(event.created_at, true)), node("span", "", event.message)));
    append(history, node("p", "detail-notice", "여기에는 배정·실행·개입 같은 엔진 상태 기록이 표시됩니다. 현재 실행기의 내부 도구 호출 기록은 제공되지 않습니다."));
    append(body, history);
    replace($("mission-detail"), body);
  }
  function renderIntervention(body, mission) {
    const form = node("form", "detail-intervention");
    const label = node("label", "", "방향을 바꾸거나 내용을 보완해 주세요"); label.htmlFor = "intervention-input";
    const input = node("textarea"); input.id = "intervention-input"; input.rows = 2; input.maxLength = 6000; input.placeholder = "예: 3D 시각 효과보다 실제 고객이 판단할 수 있는 비교 지표를 먼저 정리해 줘.";
    const submit = node("button", "button", "추가 지시 저장"); submit.type = "submit"; submit.disabled = state.detailBusy;
    const select = node("select"); select.id = "reassign-recipient"; select.setAttribute("aria-label", "새 담당 직원");
    for (const person of state.snapshot.employees.filter((item) => item.kind === "specialist" || item.kind === "pm")) { const option = node("option", "", person.name); option.value = person.id; append(select, option); }
    select.value = mission.employee_id;
    const reassign = node("button", "button", "담당 변경"); reassign.type = "button"; reassign.disabled = state.detailBusy;
    reassign.addEventListener("click", () => { if (select.value === mission.employee_id) { toast("현재 담당 직원과 같습니다."); return; } performAction("reassign", { employee_id: select.value }); });
    append(form, label, input, append(node("div", "intervention-bottom"), node("span", "small muted", "저장 후 ‘이어서 실행’으로 반영"), submit), node("p", "detail-notice", mission.workflow ? "추가 지시를 저장하면 진행 중인 작업을 멈춥니다. ‘이어서 실행’을 누르면 PM이 지시를 반영해 다시 계획합니다." : "실행 중인 경우 기존 실행을 멈추고 일시 정지합니다. 추가 지시는 다음 실행에 전달되며 자동으로 다시 시작하지 않습니다."));
    if (!mission.workflow) append(form, append(node("div", "intervention-bottom"), select, reassign));
    form.addEventListener("submit", (event) => { event.preventDefault(); const text = input.value.trim(); if (!text) { input.focus(); return; } performAction("instruct", { text }); });
    append(body, form);
  }
  async function performAction(action, fields = {}) {
    if (state.detailBusy || !state.detail) return;
    state.detailBusy = true;
    const missionId = state.detail.mission.id;
    const text = $("intervention-input")?.value || "";
    refreshDetailStatus();
    try {
      const result = await request(`/api/org/missions/${encodeURIComponent(missionId)}/action`, { action, ...fields, ...(action === "instruct" ? { context: state.detail.mission.project_context || { project_id: "office-ui" } } : {}) });
      if (state.detail?.mission.id === missionId) state.detail = result;
      await loadSnapshot().catch(() => {});
      if (action === "instruct" && $("intervention-input")) $("intervention-input").value = "";
      toast({ pause: "일시 정지를 요청했습니다.", resume: "다시 실행하도록 대기열에 넣었습니다.", cancel: "업무 취소를 요청했습니다.", instruct: "추가 지시를 저장했습니다. 일시 정지 후 이어서 실행할 수 있습니다.", reassign: "담당을 변경했습니다. 이어서 실행하면 새 담당의 기억과 역할을 사용합니다." }[action]);
    } catch (error) {
      const target = $("action-error"); if (target) { target.textContent = error.message; target.hidden = false; }
      if ($("intervention-input")) $("intervention-input").value = text;
    } finally { state.detailBusy = false; for (const button of $("mission-detail").querySelectorAll(".detail-actions button, .detail-intervention button")) button.disabled = false; }
  }

  async function submitDelivery(sourceAttempt, operation) {
    if (state.detailBusy) return;
    state.detailBusy = true; refreshDetailStatus();
    const text = { apply: "이 미리보기를 운영 화면에 반영해 줘", commit: "이 미리보기를 반영하고 커밋해 줘", push: "이 미리보기를 반영하고 커밋하고 푸시해 줘" }[operation];
    try {
      const result = await request("/api/org/missions", { text, employee_id: "das-pm", request_id: `deliver-${sourceAttempt}-${operation}`, context: { project_id: "office-ui" }, source_attempt_id: sourceAttempt, delivery_operation: operation });
      await loadSnapshot();
      await openMission(result.mission.id);
    } catch (error) {
      const target = $("action-error"); if (target) { target.textContent = error.message; target.hidden = false; }
    } finally { state.detailBusy = false; refreshDetailStatus(); }
  }

  $("command-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if (readOnly || state.submitting || !state.snapshot || !authenticated || voiceState.busy || voiceState.recording || browserSpeechState.active) return;
    const text = $("command-input").value.trim(); const recipient = recipientMode;
    if (!text) { $("command-input").focus(); return; }
    const signature = JSON.stringify([text, recipient]);
    if (!state.pending || state.pending.signature !== signature) state.pending = { signature, request_id: typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `office-${Date.now()}-${Math.random().toString(36).slice(2)}` };
    state.submitting = true; $("command-input").readOnly = true; $("command-submit").disabled = true; $("recipient").disabled = true; $("command-submit").textContent = "업무를 전달하고 있습니다…"; $("command-error").hidden = true;
    updateComposer();
    try {
      if (recipient === "auto") {
        const routed = await routeDraft();
        if (!routed || routed.needs_clarification) throw new Error("받는 직원 한 명을 선택해 주세요.");
      }
      const result = await request("/api/org/missions", { text, employee_id: recipient === "auto" ? autoRoute.employee_id : recipient, auto_recipient: recipient === "auto", request_id: state.pending.request_id, context: { project_id: "office-ui" } });
      $("command-input").value = ""; state.pending = null; autoRoute = null; ++routeRevision; voiceInput?.acknowledgeDraft();
      await loadSnapshot().catch(() => {});
      if (result.mission?.employee_id) chooseEmployee(result.mission.employee_id);
      toast(result.duplicate ? "이미 접수된 미션을 확인했습니다. 중복 실행하지 않습니다." : `${employeeName(result.mission?.employee_id)}에게 업무를 전달했습니다.`);
      $("missions").scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (error) { $("command-error").textContent = `${error.message} 같은 내용으로 다시 누르면 중복 접수를 방지합니다.`; $("command-error").hidden = false; }
    finally { state.submitting = false; $("command-input").readOnly = false; $("recipient").disabled = !state.snapshot; $("command-submit").textContent = "업무 맡기기 ↗"; updateComposer(); }
  });
  $("command-input").addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); $("command-form").requestSubmit(); } });
  $("command-input").addEventListener("input", () => { ++routeRevision; autoRoute = null; clearTimeout(routeTimer); updateComposer(); routeTimer = setTimeout(() => routeDraft().catch(() => { $("recipient-hint").textContent = "직원 선택을 확인하지 못했습니다. 직접 선택하거나 다시 시도해 주세요."; }), 350); });
  $("recipient").addEventListener("change", () => { recipientMode = $("recipient").value; ++routeRevision; if (recipientMode !== "auto") chooseEmployee(recipientMode); else routeDraft().catch(() => {}); updateComposer(); });
  $("assign-selected").addEventListener("click", () => chooseEmployee(state.selected, true));
  $("mission-filter").addEventListener("change", () => { state.filter = $("mission-filter").value; if (state.snapshot) renderMissions(); });
  $("close-mission").addEventListener("click", () => $("mission-dialog").close());
  $("mission-dialog").addEventListener("close", () => { state.detailRequest++; state.detail = null; });
  $("mission-dialog").addEventListener("click", (event) => { if (event.target === $("mission-dialog")) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close(); } });
  for (const button of document.querySelectorAll("[data-tab]")) {
    button.addEventListener("click", () => { state.tab = button.dataset.tab; if (state.snapshot) renderInspector(); });
    button.addEventListener("keydown", (event) => {
      const tabs = [...document.querySelectorAll("[data-tab]")]; const current = tabs.indexOf(button);
      const index = event.key === "ArrowRight" ? (current + 1) % tabs.length : event.key === "ArrowLeft" ? (current - 1 + tabs.length) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
      if (index >= 0) { event.preventDefault(); tabs[index].click(); tabs[index].focus(); }
    });
  }
  $("reconnect").addEventListener("click", async () => { setConnection(false, "연결 확인 중"); try { if (await checkAuth()) { await loadSnapshot(); connectStream(); } } catch {} });
  $("today").textContent = new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "short" }).format(new Date());
  try {
    const saved = sessionStorage.getItem("daslab.office.voiceDraft");
    if (saved) {
      const draft = JSON.parse(saved);
      if (typeof draft.text === "string" && draft.text.trim()) { state.voiceDraft = draft; $("command-input").value = draft.text.slice(0, 6000); if (typeof draft.employee_id === "string") { state.selected = draft.employee_id; recipientMode = draft.employee_id; } }
      sessionStorage.removeItem("daslab.office.voiceDraft");
    }
  } catch { notice("음성 초안을 불러오지 못했습니다. 음성 화면의 텍스트를 직접 옮길 수 있습니다."); }
  function appendDictationTranscript({text}) {
    const input = $("command-input");
    const combined = [input.value.trim(), text].filter(Boolean).join("\n");
    if (combined.length > input.maxLength) { toast("입력 공간이 부족합니다. 기존 내용을 먼저 정리한 뒤 다시 말해 주세요."); return false; }
    input.value = combined;
    ++routeRevision;
    routeDraft().catch(() => { $("recipient-hint").textContent = "받는 직원을 직접 선택해 주세요."; });
    toast("말씀을 입력했습니다. 내용과 받는 직원을 확인하고 맡겨 주세요.");
    return true;
  }
  voiceInput = new window.DASVoiceInput.InlineVoiceInput({
    isAuthenticated: () => authenticated && voiceEnabled,
    onState: (value) => { voiceState = value; $("voice-status").textContent = readOnly ? "미리보기에서는 음성 입력을 사용할 수 없습니다." : value.message; updateComposer(); },
    onTranscript: appendDictationTranscript,
  });
  browserSpeechInput = new BrowserSpeechInput({
    isAllowed: () => authenticated && voiceEnabled && !readOnly && !state.submitting,
    onState: (value) => { browserSpeechState = value; $("browser-speech-status").textContent = readOnly ? "미리보기에서는 음성 입력을 사용할 수 없습니다." : value.message; updateComposer(); },
    onTranscript: appendDictationTranscript,
  });
  $("keyboard-dictation").addEventListener("click", () => {
    browserSpeechInput.cancel();
    if (voiceState.recording || voiceState.busy) void voiceInput.cancel();
    $("command-input").focus();
  });
  $("local-voice-disclosure").addEventListener("click", () => {
    const expanded = $("local-voice-disclosure").getAttribute("aria-expanded") === "true";
    $("local-voice-disclosure").setAttribute("aria-expanded", String(!expanded));
    $("local-voice-disclosure").textContent = expanded ? "앱 자체 음성인식 (실험) 펼치기" : "앱 자체 음성인식 (실험) 접기";
    $("inline-voice").classList.toggle("mobile-expanded", !expanded);
    if (expanded) void voiceInput.cancel();
  });
  $("browser-speech-toggle").addEventListener("click", () => { if (!voiceState.recording && !voiceState.busy) browserSpeechInput.toggle(); });
  $("browser-speech-cancel").addEventListener("click", () => browserSpeechInput.cancel());
  $("voice-toggle").addEventListener("click", () => { if (!browserSpeechState.active) voiceInput.toggle(); });
  $("voice-cancel").addEventListener("click", () => voiceInput.cancel());
  $("pair-device").addEventListener("click", startPairing);
  $("revoke-devices").addEventListener("click", async () => {
    if (!authenticated || $("revoke-devices").hidden || !confirm("연결된 모든 휴대폰의 로그인을 해제할까요? 다시 연결하려면 새 QR 코드를 스캔해야 합니다.")) return;
    $("revoke-devices").disabled = true;
    $("pair-device").disabled = true;
    try {
      const result = await request("/api/owner/pair/revoke-all", {});
      ++pairRequest;
      clearPairCode();
      if ($("pair-dialog").open) { markPairClosing(); $("pair-dialog").close(); }
      toast(`연결된 휴대폰 ${Number(result.revoked_count) || 0}대의 로그인을 해제했습니다.`);
    } catch (error) { notice(error.message); }
    finally { $("revoke-devices").disabled = pairClosing; $("pair-device").disabled = pairClosing; }
  });
  $("close-pair").addEventListener("click", () => { markPairClosing(); $("pair-dialog").close(); });
  $("pair-dialog").addEventListener("cancel", markPairClosing);
  $("pair-dialog").addEventListener("close", async () => {
    ++pairRequest;
    clearPairCode();
    markPairClosing();
    try {
      // Wait for a late start response before invalidating the pending token.
      if (pairStartPromise) await pairStartPromise.catch(() => {});
      await request("/api/owner/pair/cancel", {});
    } catch { notice("연결 코드를 바로 해제하지 못했습니다. 10분 뒤 만료됩니다."); }
    finally { pairClosing = false; $("pair-device").disabled = false; $("revoke-devices").disabled = false; }
  });
  $("copy-pair").addEventListener("click", async () => {
    if (!pairExpiresAt || pairExpiresAt <= Date.now()) { clearPairCode("연결 코드가 만료됐습니다. ‘휴대폰 연결’을 다시 눌러 주세요."); return; }
    const link = $("pair-link");
    try {
      await navigator.clipboard.writeText(link.value);
      $("pair-status").textContent = "주소를 복사했습니다. 휴대폰에서 열어 주세요.";
    } catch {
      link.select();
      $("pair-status").textContent = "주소를 선택했습니다. 복사해서 휴대폰에서 열어 주세요.";
    }
  });
  $("logout").addEventListener("click", async () => {
    browserSpeechInput.cancel();
    voiceInput.setEnabled(false);
    $("logout").disabled = true;
    try {
      const response = await fetch("/auth/logout", {method: "POST", headers: {"Content-Type": "application/json", "X-DAS-Office": "1"}, body: "{}"});
      if (!response.ok) throw new Error("로그아웃하지 못했습니다. 다시 눌러 주세요.");
      requireLogin({discardDraft: true});
    } catch (error) { notice(error.message); $("logout").disabled = false; voiceInput.setEnabled(authenticated); }
  });
  checkAuth().then(async (allowed) => {
    if (!allowed) return;
    await loadSnapshot();
    try {
      const saved = JSON.parse(sessionStorage.getItem("daslab.office.authDraft") || "null");
      sessionStorage.removeItem("daslab.office.authDraft");
      if (saved && typeof saved.text === "string" && Date.now() - saved.savedAt < 86400000 && !$("command-input").value) {
        $("command-input").value = saved.text.slice(0, 6000);
        recipientMode = saved.recipient === "auto" || employee(saved.recipient) ? saved.recipient : "auto";
        const signature = JSON.stringify([$("command-input").value.trim(), recipientMode]);
        if (saved.pending?.signature === signature && typeof saved.pending.request_id === "string" && saved.pending.request_id.length <= 100) state.pending = saved.pending;
        renderRecipient(); await routeDraft();
        toast("로그인 전에 작성하던 내용을 복구했습니다.");
      }
    } catch { /* A malformed or unavailable local draft must not prevent loading. */ }
    if (state.voiceDraft) { toast("음성 내용을 가져왔습니다. 확인한 뒤 ‘업무 맡기기’를 눌러 주세요."); $("command-input").focus(); }
    connectStream();
  }).catch(() => {});
  setInterval(() => { if (!state.snapshot || !state.connected) return; for (const element of document.querySelectorAll("[data-mission-time]")) { const mission = state.snapshot.missions.find((item) => item.id === element.dataset.missionTime); if (mission) element.textContent = `실행 ${missionDuration(mission)}`; } }, 1000);
  window.addEventListener("pagehide", () => { browserSpeechInput.cancel(); state.source?.close(); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) checkAuth().then((allowed) => { if (allowed) { loadSnapshot().catch(() => {}); connectStream(); } }).catch(() => {}); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) browserSpeechInput.cancel(); else checkAuth().catch(() => {}); });
})();
