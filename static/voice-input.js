/* Optional main-page dictation. No enrollment, background listening, or submission. */
(function (root) {
  'use strict';

  const MAX_SECONDS = 60;
  const KEYBOARD = '직접 입력하거나 휴대전화 키보드의 음성 입력을 이용해 주세요.';

  async function loadLocalRuntime() {
    const [audio, rpc] = await Promise.all([
      import('./voice/audio.mjs'), import('./voice/rpc.mjs'),
    ]);
    return {createEngine: () => new rpc.VoiceEngine(), createMicrophone: () => new audio.Microphone(),
      resample: audio.resample, join: audio.join};
  }

  function browserIssue(host) {
    if (!host.isSecureContext) return '휴대전화의 마이크는 보안 연결(HTTPS)에서 사용할 수 있어요. ' + KEYBOARD;
    if (!host.navigator?.mediaDevices?.getUserMedia || !host.Worker || !host.AudioContext
        || !host.AudioWorkletNode || !host.OfflineAudioContext) {
      return '이 브라우저에서는 기기 내 음성 입력을 사용할 수 없어요. ' + KEYBOARD;
    }
    return '';
  }

  function friendlyError(error) {
    if (error?.name === 'NotAllowedError' || error?.name === 'SecurityError') {
      return '마이크 사용이 허용되지 않았어요. 브라우저의 사이트 설정에서 허용하거나 직접 입력해 주세요.';
    }
    if (error?.name === 'NotFoundError') return '사용할 마이크를 찾지 못했어요. ' + KEYBOARD;
    if (error?.name === 'NotReadableError' || error?.name === 'AbortError') {
      return '마이크를 사용할 수 없어요. 다른 앱의 녹음을 끝내고 다시 눌러 주세요.';
    }
    if (error?.voiceInputMessage) return error.message;
    return '기기 내 음성 입력을 준비하거나 처리하지 못했어요. 다시 누르거나 직접 입력해 주세요.';
  }

  function inputError(message) {
    const error = new Error(message);
    error.voiceInputMessage = true;
    return error;
  }

  function checkAudio(audio) {
    if (!(audio instanceof Float32Array) || audio.length < 4000 || audio.length > 16000 * MAX_SECONDS) {
      throw inputError('말씀이 너무 짧거나 길어요. 1~60초 안으로 다시 말씀해 주세요.');
    }
    let energy = 0, clipped = 0;
    for (const sample of audio) {
      if (!Number.isFinite(sample) || Math.abs(sample) > 1.0001) throw inputError('소리를 제대로 받지 못했어요. 다시 말씀해 주세요.');
      energy += sample * sample;
      if (Math.abs(sample) >= .995) clipped++;
    }
    if (Math.sqrt(energy / audio.length) < .003) throw inputError('말소리가 잘 들리지 않았어요. 마이크 가까이에서 다시 말씀해 주세요.');
    if (clipped / audio.length > .015) throw inputError('소리가 너무 크게 들어왔어요. 마이크에서 조금 떨어져 다시 말씀해 주세요.');
  }

  class InlineVoiceInput {
    constructor(options = {}) {
      this.host = options.host || root;
      this.document = options.document || this.host.document;
      this.isAuthenticated = options.isAuthenticated || (() => false);
      this.onState = options.onState || (() => {});
      this.onTranscript = options.onTranscript || (() => {});
      this.onClear = options.onClear || (() => {});
      this.loadRuntime = options.loadRuntime || loadLocalRuntime;
      this.getBrowserIssue = options.getBrowserIssue || (() => browserIssue(this.host));
      this.enabled = options.enabled !== false;
      this.disposed = false;
      this.epoch = 0;
      this.session = null;
      this.engine = null;
      this.engineReady = false;
      this.lastTranscript = null;
      this.state = 'idle';
      this.message = '마이크를 눌러 말씀하세요.';
      this.hidden = () => { if (this.document?.hidden) void this.cancel(); };
      this.pagehide = () => { void this.cancel(); };
      this.document?.addEventListener('visibilitychange', this.hidden);
      this.host.addEventListener?.('pagehide', this.pagehide);
      this._emit();
    }

    _allowed() {
      try { return !this.disposed && this.enabled && this.isAuthenticated() === true; }
      catch { return false; }
    }

    _emit(state = this.state, message = this.message) {
      this.state = state;
      this.message = message;
      const enabled = this._allowed();
      this.onState({state: enabled ? state : 'disabled', message: enabled ? message : '로그인한 뒤 음성 입력을 사용하세요.',
        enabled, recording: enabled && state === 'listening', busy: ['loading', 'processing'].includes(state),
        canStart: enabled && !this.session, canStop: enabled && state === 'listening',
        canCancel: !!this.session});
    }

    _current(session) {
      if (this.session !== session || session.epoch !== this.epoch) return false;
      if (!this._allowed()) {
        void this.cancel({clearDraft: true});
        return false;
      }
      if (this.document?.hidden) { void this.cancel(); return false; }
      return true;
    }

    _wipe(session) {
      for (const chunk of session?.chunks || []) chunk.fill(0);
      if (session) session.chunks = [];
      session?.raw?.fill(0);
      session?.audio?.fill(0);
    }

    async _stopMic(session) {
      if (!session) return;
      this.host.clearTimeout(session.timer);
      const mic = session.mic;
      session.mic = null;
      if (mic) await mic.stop();
    }

    async start() {
      if (this.session || !this._allowed()) { this._emit(); return false; }
      if (this.document?.hidden) return false;
      const issue = this.getBrowserIssue();
      if (issue) { this._emit('error', issue); return false; }
      const session = this.session = {epoch: ++this.epoch, chunks: [], rate: null, samples: 0, mic: null, timer: null};
      this._emit('loading', '음성 입력을 준비하고 있어요. 처음에는 잠시 걸릴 수 있어요.');
      try {
        const runtime = await this.loadRuntime();
        if (!this._current(session)) return false;
        session.runtime = runtime;
        if (!this.engine) { this.engine = runtime.createEngine(); this.engineReady = false; }
        session.engine = this.engine;
        if (!this.engineReady) {
          // Unlike legacy init, this loads only Whisper, not speaker models.
          await session.engine.request('init-asr');
          if (!this._current(session)) return false;
          this.engineReady = true;
        }
        session.mic = runtime.createMicrophone();
        this._emit('loading', '마이크 사용을 허용해 주세요. 준비되면 말씀하시면 됩니다.');
        await session.mic.start((chunk, rate) => this._chunk(session, chunk, rate));
        if (!this._current(session)) { await this._stopMic(session); return false; }
        this._emit('listening', '말씀하세요. 끝나면 마이크를 다시 눌러 주세요. 60초가 되면 자동으로 글자로 바꿉니다.');
        session.timer = this.host.setTimeout(() => {
          if (this._current(session)) void this.stop();
        }, MAX_SECONDS * 1000);
        return true;
      } catch (error) {
        if (this._current(session)) await this._fail(session, error);
        else await this._stopMic(session);
        return false;
      }
    }

    _chunk(session, chunk, rate) {
      if (!(chunk instanceof Float32Array)) return;
      if (!this._current(session) || this.state !== 'listening') { chunk.fill(0); return; }
      if (!Number.isFinite(rate) || rate < 8000 || rate > 192000 || (session.rate && rate !== session.rate)) {
        chunk.fill(0);
        void this._fail(session, inputError('마이크 소리를 제대로 받지 못했어요. 다시 눌러 주세요.'));
        return;
      }
      session.rate = rate;
      const remaining = Math.max(0, Math.floor(MAX_SECONDS * rate - session.samples));
      if (chunk.length >= remaining) {
        const retained = chunk.slice(0, remaining);
        chunk.fill(0);
        if (retained.length) session.chunks.push(retained);
        session.samples += retained.length;
        void this.stop();
        return;
      }
      session.chunks.push(chunk);
      session.samples += chunk.length;
    }

    async stop() {
      const session = this.session;
      if (!session || this.state !== 'listening') return false;
      this._emit('processing', '말씀을 글자로 바꾸고 있어요. 마이크는 끕니다.');
      try {
        await this._stopMic(session);
        if (!this._current(session)) return false;
        if (!session.chunks.length || !session.rate) throw inputError('말소리를 받지 못했어요. 다시 말씀해 주세요.');
        session.raw = session.runtime.join(session.chunks);
        for (const chunk of session.chunks) chunk.fill(0);
        session.chunks = [];
        session.audio = await session.runtime.resample(session.raw, session.rate);
        session.raw.fill(0);
        if (!this._current(session)) return false;
        checkAudio(session.audio);
        const result = await session.engine.request('transcribe-inline', session.audio);
        if (!this._current(session)) return false;
        if (typeof result?.text !== 'string' || !result.text.trim() || result.text.length > 2000) {
          throw inputError('말씀을 글자로 확인하지 못했어요. 다시 말하거나 직접 입력해 주세요.');
        }
        const text = result.text.trim();
        this.session = null;
        this.lastTranscript = text;
        this._emit('ready', '입력 내용을 확인하고 맡기기를 눌러 주세요.');
        if (session.epoch !== this.epoch || !this._allowed() || this.document?.hidden) return false;
        this.onTranscript({text});
        return true;
      } catch (error) {
        if (this._current(session)) await this._fail(session, error);
        return false;
      } finally { this._wipe(session); }
    }

    async _fail(session, error) {
      if (this.session !== session) return;
      const failureEpoch = this.epoch + 1;
      await this.cancel();
      if (this.epoch === failureEpoch && !this.session && this._allowed() && !this.document?.hidden) {
        this._emit('error', friendlyError(error));
      }
    }

    async toggle() {
      if (this.state === 'listening') return this.stop();
      if (this.session) { await this.cancel(); return false; }
      return this.start();
    }

    async cancel({clearDraft = false} = {}) {
      const session = this.session;
      ++this.epoch;
      this.session = null;
      this._wipe(session);
      const engine = this.engine;
      this.engine = null;
      this.engineReady = false;
      engine?.close();
      if (clearDraft && this.lastTranscript !== null) {
        const text = this.lastTranscript;
        this.lastTranscript = null;
        this.onClear({text});
      }
      this._emit('idle', '마이크가 꺼졌어요. 다시 누르면 음성으로 입력할 수 있어요.');
      await this._stopMic(session);
    }

    setEnabled(enabled) {
      this.enabled = enabled === true;
      if (!this.enabled) return this.cancel({clearDraft: true});
      this._emit();
      return Promise.resolve();
    }

    acknowledgeDraft() { this.lastTranscript = null; }

    async destroy() {
      this.disposed = true;
      this.document?.removeEventListener('visibilitychange', this.hidden);
      this.host.removeEventListener?.('pagehide', this.pagehide);
      await this.cancel();
    }
  }

  const api = {InlineVoiceInput};
  root.DASVoiceInput = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
