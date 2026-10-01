// Synthetic PCM and fake browser resources only; never opens a microphone.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import voice from '../static/voice-input.js';

const {InlineVoiceInput} = voice;
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject}; };
const flush = () => new Promise(resolve => setImmediate(resolve));
const audio = (seconds = 1, value = .08) => new Float32Array(Math.round(seconds * 16000)).fill(value);

function fixture(changes = {}) {
  const listeners = new Map(), timers = new Map(), timerDelays = [];
  let nextTimer = 0;
  const document = {hidden: false, addEventListener: (type, fn) => listeners.set(type, fn), removeEventListener: type => listeners.delete(type)};
  const host = {document, addEventListener: (type, fn) => listeners.set(type, fn), removeEventListener: type => listeners.delete(type),
    setTimeout: (fn, ms) => {timerDelays.push(ms); timers.set(++nextTimer, fn); return nextTimer;}, clearTimeout: id => timers.delete(id)};
  const data = {auth: true, runtimeLoads: 0, engines: [], mics: [], states: [], transcripts: [], clears: [], transferred: [], received: [], listeners, timers, timerDelays, document, host};
  const runtime = {
    createEngine() {
      const engine = {calls: [], closed: false, async request(type, samples) {
        this.calls.push(type);
        if (type === 'init-asr') return changes.init ? changes.init.promise : {};
        assert.equal(type, 'transcribe-inline', 'no speaker or enrollment requests');
        data.transferred.push(samples);
        data.received.push({length: samples.length, first: samples[0], last: samples.at(-1)});
        return changes.transcription ? changes.transcription.promise : {text: 'PM 너가 이 작업 진행해 줘'};
      }, close() {this.closed = true;}};
      data.engines.push(engine);
      return engine;
    },
    createMicrophone() {
      const mic = {stopped: false, starts: 0, async start(callback) {
        this.callback = callback; this.starts++;
        if (changes.permissionError) throw changes.permissionError;
        if (changes.permission) await changes.permission.promise;
        // Like the real Microphone generation guard, late permission remains stopped.
      }, async stop() {this.stopped = true;}, push(samples, rate = 16000) {this.callback(samples, rate);}};
      data.mics.push(mic);
      return mic;
    },
    join(chunks) {const value = new Float32Array(chunks.reduce((sum, part) => sum + part.length, 0)); let offset = 0; for (const part of chunks) {value.set(part, offset); offset += part.length;} return value;},
    async resample(raw) {return changes.resample ? changes.resample.promise : raw.slice();},
  };
  data.controller = new InlineVoiceInput({host, document, isAuthenticated: () => data.auth,
    getBrowserIssue: () => changes.issue || '',
    loadRuntime: async () => {data.runtimeLoads++; return changes.runtime ? changes.runtime.promise : runtime;},
    onState: state => data.states.push(state), onTranscript: result => data.transcripts.push(result), onClear: value => data.clears.push(value)});
  data.runtime = runtime;
  return data;
}

test('construction/login changes never load models or request a microphone', async t => {
  const f = fixture(); t.after(() => f.controller.destroy());
  assert.equal(f.runtimeLoads, 0); assert.equal(f.mics.length, 0); assert.equal(f.engines.length, 0);
  f.auth = false;
  assert.equal(await f.controller.start(), false);
  await f.controller.setEnabled(false); f.auth = true; await f.controller.setEnabled(true);
  assert.equal(f.runtimeLoads, 0);
  assert.equal(f.states.at(-1).canStart, true);
});

test('click initializes only ASR, records once, stops microphone and returns an unsubmitted draft', async t => {
  const f = fixture(); t.after(() => f.controller.destroy());
  assert.equal(await f.controller.start(), true);
  assert.deepEqual(f.engines[0].calls, ['init-asr']);
  assert.equal(await f.controller.start(), false, 'duplicate start cannot open a second microphone');
  const chunk = audio(); f.mics[0].push(chunk);
  assert.equal(await f.controller.stop(), true);
  assert.equal(f.mics[0].stopped, true);
  assert.deepEqual(f.engines[0].calls, ['init-asr', 'transcribe-inline']);
  assert.deepEqual(f.transcripts, [{text: 'PM 너가 이 작업 진행해 줘'}]);
  assert.ok(chunk.every(value => value === 0));
  assert.ok(f.transferred[0].every(value => value === 0));
  assert.equal(f.states.at(-1).state, 'ready');
  assert.match(f.states.at(-1).message, /확인.*맡기기/);
  assert.equal(f.timers.size, 0);
  await f.controller.start();
  assert.equal(f.engines.length, 1, 'successful repeated dictation reuses the local ASR model');
  assert.deepEqual(f.engines[0].calls, ['init-asr', 'transcribe-inline']);
});

test('model initialization cancellation cannot start a microphone later', async t => {
  const init = deferred(), f = fixture({init}); t.after(() => f.controller.destroy());
  const starting = f.controller.start(); await flush();
  assert.equal(f.mics.length, 0);
  await f.controller.cancel(); init.resolve({});
  assert.equal(await starting, false);
  assert.equal(f.mics.length, 0); assert.equal(f.engines[0].closed, true);
  assert.deepEqual(f.transcripts, []);
});

test('logout during permission request stops late startup and drops late chunks', async t => {
  const permission = deferred(), f = fixture({permission}); t.after(() => f.controller.destroy());
  const starting = f.controller.start(); await flush();
  const mic = f.mics[0];
  f.auth = false; await f.controller.setEnabled(false);
  permission.resolve(); assert.equal(await starting, false);
  const late = audio(); mic.push(late);
  assert.equal(mic.stopped, true); assert.ok(late.every(value => value === 0));
  assert.equal(f.states.at(-1).state, 'disabled'); assert.deepEqual(f.transcripts, []);
});

test('visibility/pagehide discard in-flight transcription, not a completed draft', async t => {
  for (const event of ['visibilitychange', 'pagehide']) {
    const transcription = deferred(), f = fixture({transcription}); t.after(() => f.controller.destroy());
    await f.controller.start(); f.mics[0].push(audio());
    const stopping = f.controller.stop(); await flush();
    if (event === 'visibilitychange') f.document.hidden = true;
    f.listeners.get(event)();
    transcription.resolve({text: '늦게 나온 요청'}); assert.equal(await stopping, false);
    assert.deepEqual(f.transcripts, []); assert.equal(f.engines[0].closed, true); assert.equal(f.mics[0].stopped, true);
    assert.ok(f.transferred[0].every(value => value === 0));
    assert.deepEqual(f.clears, []);
    const complete = fixture(); t.after(() => complete.controller.destroy());
    await complete.controller.start(); complete.mics[0].push(audio()); await complete.controller.stop();
    if (event === 'visibilitychange') complete.document.hidden = true;
    complete.listeners.get(event)(); await flush();
    assert.deepEqual(complete.clears, [], 'completed text survives a temporary tab switch');
    assert.equal(complete.transcripts.length, 1);
    await complete.controller.setEnabled(false);
    assert.deepEqual(complete.clears, [{text: 'PM 너가 이 작업 진행해 줘'}], 'logout alone clears unacknowledged voice text');
  }
});

test('authentication is rechecked at transcription completion even without an explicit logout event', async t => {
  const transcription = deferred(), f = fixture({transcription}); t.after(() => f.controller.destroy());
  await f.controller.start(); f.mics[0].push(audio());
  const stopping = f.controller.stop(); await flush(); f.auth = false;
  transcription.resolve({text: '허용하면 안 되는 늦은 요청'});
  assert.equal(await stopping, false); assert.deepEqual(f.transcripts, []);
  assert.equal(f.engines[0].closed, true);
});

test('silence and malformed audio never reach ASR', async t => {
  for (const samples of [audio(1, 0), audio(1, 1), audio(.1), new Float32Array([NaN])]) {
    const f = fixture(); t.after(() => f.controller.destroy());
    await f.controller.start(); f.mics[0].push(samples); await f.controller.stop();
    assert.deepEqual(f.engines[0].calls, ['init-asr']); assert.deepEqual(f.transcripts, []);
    assert.equal(f.states.at(-1).state, 'error');
  }
});

test('60 second cap preserves the recorded prefix and automatically transcribes once', async t => {
  for (const duration of [60, 60.01]) {
    const f = fixture(); t.after(() => f.controller.destroy());
    await f.controller.start(); const samples = audio(duration); f.mics[0].push(samples);
    const late = audio(); f.mics[0].push(late); await flush();
    assert.equal(f.mics[0].stopped, true); assert.ok(samples.every(value => value === 0));
    assert.ok(late.every(value => value === 0));
    assert.deepEqual(f.engines[0].calls, ['init-asr', 'transcribe-inline']);
    assert.equal(f.received[0].length, 60 * 16000);
    assert.ok(Math.abs(f.received[0].first - .08) < .00001);
    assert.ok(Math.abs(f.received[0].last - .08) < .00001);
    assert.equal(f.transcripts.length, 1); assert.equal(f.states.at(-1).state, 'ready');
  }
});

test('duration timer stops and transcribes collected audio with no further audio events', async t => {
  const f = fixture(); t.after(() => f.controller.destroy());
  await f.controller.start(); f.mics[0].push(audio());
  assert.deepEqual(f.timerDelays, [60000]);
  [...f.timers.values()][0](); await flush();
  assert.equal(f.mics[0].stopped, true); assert.equal(f.transcripts.length, 1);
  assert.deepEqual(f.engines[0].calls, ['init-asr', 'transcribe-inline']);
  assert.equal(f.timers.size, 0);
});

test('unavailable browser and denied permission show a manual alternative without a cloud fallback', async t => {
  const f = fixture({issue: 'HTTPS에서 열거나 키보드로 입력해 주세요.'}); t.after(() => f.controller.destroy());
  assert.equal(await f.controller.start(), false); assert.equal(f.runtimeLoads, 0);
  assert.match(f.states.at(-1).message, /키보드/);
  const denied = fixture({permissionError: Object.assign(new Error('denied'), {name: 'NotAllowedError'})});
  t.after(() => denied.controller.destroy());
  assert.equal(await denied.controller.start(), false); assert.equal(denied.mics[0].stopped, true);
  assert.match(denied.states.at(-1).message, /허용/); assert.deepEqual(denied.transcripts, []);
});

test('acknowledged or manually replaced draft is not cleared as pending voice text', async t => {
  const f = fixture(); t.after(() => f.controller.destroy());
  await f.controller.start(); f.mics[0].push(audio()); await f.controller.stop();
  f.controller.acknowledgeDraft(); await f.controller.setEnabled(false);
  assert.deepEqual(f.clears, []);
});

test('destroy stops resources without clearing completed input', async () => {
  const f = fixture();
  await f.controller.start(); f.mics[0].push(audio()); await f.controller.stop();
  await f.controller.destroy();
  assert.equal(f.engines[0].closed, true); assert.equal(f.listeners.size, 0);
  assert.deepEqual(f.clears, []); assert.equal(await f.controller.start(), false);
});

test('controller contains no enrollment, persistence, upload, or implicit speech API execution', async () => {
  const source = await readFile(new URL('../static/voice-input.js', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /localStorage|sessionStorage|SpeechRecognition|webkitSpeechRecognition|fetch\s*\(|XMLHttpRequest|\/api\/org|\.request\(['"](?:init|embed|segment)['"]/);
});
