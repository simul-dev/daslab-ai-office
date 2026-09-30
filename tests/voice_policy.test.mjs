// Synthetic vectors/audio test decision logic only, not real speaker accuracy or calibrated thresholds.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  MODEL_ID, PROFILE_VERSION, SAMPLE_RATE, MAX_SECONDS, normalise, readProfile,
  createProfile, signalQuality, assessSegment, verifyEmbeddings, verificationWindows,
  parseWake, WakeGate, riskHint,
} from '../static/voice/policy.mjs';
import {Microphone, Utterances} from '../static/voice/audio.mjs';

const NOW = Date.parse('2026-09-24T10:00:00Z');
const vector = (angle = 0) => {
  const result = new Array(256).fill(0);
  result[0] = Math.cos(angle); result[1] = Math.sin(angle);
  return result;
};
const owner = () => createProfile([vector(0), vector(.08), vector(-.06)], NOW);
const segment = (changes = {}) => ({overlapMax:.1, overlapSeconds:0, speechSeconds:3,
  uncertainSeconds:0, speakerCount:1, ...changes});
const audio = (seconds = 2, amplitude = .08) => new Float32Array(Math.round(seconds * SAMPLE_RATE)).fill(amplitude);

test('unenrolled or damaged speaker profiles cannot match', () => {
  for (const profile of [null, {}, {...owner(), model:'other-model'}, {...owner(), version:99}]) {
    assert.equal(verifyEmbeddings(profile, [vector()]).ok, false);
  }
  assert.equal(verifyEmbeddings(owner(), []).ok, false);
  assert.equal(verifyEmbeddings(owner(), [[1, 2, 3]]).ok, false);
});

test('enrollment needs three consistent, finite speaker vectors', () => {
  const profile = owner();
  assert.equal(profile.version, PROFILE_VERSION);
  assert.equal(profile.model, MODEL_ID);
  assert.equal(profile.embeddings.length, 3);
  assert.equal(profile.createdAt, new Date(NOW).toISOString());
  assert.equal(verifyEmbeddings(profile, [vector(.02)]).ok, true);
  assert.throws(() => createProfile([vector(), vector()], NOW));
  assert.throws(() => createProfile([vector(), vector(), vector(Math.PI / 2)], NOW));
  assert.throws(() => createProfile([vector(), vector(), new Array(256).fill(0)], NOW));
  const invalid = vector(); invalid[9] = NaN;
  assert.throws(() => createProfile([vector(), vector(), invalid], NOW));
});

test('saved profile validation rejects incompatible or malformed data', () => {
  const profile = owner();
  assert.ok(readProfile(profile));
  const invalid = [null, {}, {...profile, embeddings:[]}, {...profile, embeddings:[vector(), vector(), [1]]},
    {...profile, threshold:NaN}, {...profile, threshold:.59}, {...profile, threshold:.86},
    {...profile, createdAt:''}, {...profile, version:PROFILE_VERSION + 1}, {...profile, model:'unknown'}];
  for (const value of invalid) assert.equal(readProfile(value), null);
  assert.throws(() => normalise(new Array(256).fill(0)));
  const nonfinite = vector(); nonfinite[0] = Infinity;
  assert.throws(() => normalise(nonfinite));
});

test('silence, excessive clipping and malformed audio are rejected before inference', () => {
  assert.equal(signalQuality(audio()).ok, true);
  assert.equal(signalQuality(audio(2, 0)).reason, 'quiet_audio');
  assert.equal(signalQuality(audio(2, .001)).reason, 'quiet_audio');
  assert.equal(signalQuality(audio(2, 1)).reason, 'clipped_audio');
  assert.equal(signalQuality(new Float32Array()).reason, 'invalid_audio');
  assert.equal(signalQuality([.1, .2]).reason, 'invalid_audio');
  const corrupt = audio(); corrupt[123] = NaN;
  assert.equal(signalQuality(corrupt).reason, 'invalid_audio');
});

test('overlap, speaker change, uncertain speech and too little speech fail closed', () => {
  assert.equal(assessSegment(segment()).ok, true);
  assert.equal(assessSegment(segment({overlapSeconds:.2})).reason, 'overlap');
  assert.equal(assessSegment(segment({overlapMax:.9})).reason, 'overlap');
  assert.equal(assessSegment(segment({speakerCount:2})).reason, 'speaker_change');
  assert.equal(assessSegment(segment({uncertainSeconds:.4})).reason, 'uncertain_speech');
  assert.equal(assessSegment(segment({speechSeconds:.5})).reason, 'short_speech');
  assert.equal(assessSegment(segment({speechSeconds:2}), 2.5).reason, 'short_speech');
  assert.equal(assessSegment(null).ok, false);
  assert.equal(assessSegment(segment({speechSeconds:NaN})).ok, false);
});

test('missing or impossible speaker counts are not treated as one known speaker', () => {
  for (const speakerCount of [undefined, null, NaN, Infinity, 0, -1, 1.5, '1']) {
    assert.equal(assessSegment(segment({speakerCount})).ok, false, `speakerCount ${String(speakerCount)}`);
  }
});

test('one nonmatching verification window rejects the whole command', () => {
  assert.equal(verifyEmbeddings(owner(), [vector(), vector(.01), vector(-.02)]).ok, true);
  const result = verifyEmbeddings(owner(), [vector(), vector(Math.PI / 2), vector()]);
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'different_voice');
});

test('verification windows cover the whole command including a short final tail', () => {
  for (const seconds of [2, 3, 3.25, 5, 8.25, 11.5, 12]) {
    const input = Float32Array.from({length:Math.round(seconds * SAMPLE_RATE)}, (_, index) => index);
    const windows = verificationWindows(input), coverage = new Uint8Array(input.length);
    for (const window of windows) {
      assert.ok(window.length > 0 && window.length <= 3 * SAMPLE_RATE);
      const start = window[0];
      coverage.fill(1, start, start + window.length);
    }
    assert.equal(coverage.every(Boolean), true, `${seconds}s has uncovered samples`);
    windows[0][0] = -1;
    assert.equal(input[0], 0, 'verification windows must not alias the source buffer');
  }
});

test('Korean and English-spelled wake words only count at the beginning', () => {
  assert.deepEqual(parseWake('GPT야, 오늘 업무 정리해 줘'), {command:'오늘 업무 정리해 줘'});
  assert.deepEqual(parseWake('지피티야 오늘 업무 정리해 줘'), {command:'오늘 업무 정리해 줘'});
  assert.deepEqual(parseWake('지 피 티야 오늘 업무 정리해 줘'), {command:'오늘 업무 정리해 줘'});
  assert.equal(parseWake('동료가 GPT야 오늘 업무 정리해 줘라고 말했다'), null);
  assert.equal(parseWake('오늘 업무 정리해 줘'), null);
  assert.equal(parseWake(null), null);
});

test('wake and command in one verified utterance consume the gate once', () => {
  const gate = new WakeGate();
  assert.deepEqual(gate.accept('GPT야 오늘 업무 정리해 줘', true, NOW), {type:'command', text:'오늘 업무 정리해 줘'});
  assert.deepEqual(gate.accept('추가 업무도 진행해 줘', true, NOW + 1), {type:'no_wake'});
});

test('wake-only speech does not authorize an unverified next speaker', () => {
  const gate = new WakeGate();
  assert.deepEqual(gate.accept('GPT야', true, NOW), {type:'wake'});
  assert.deepEqual(gate.accept('보고서를 만들어 줘', false, NOW + 1000), {type:'rejected'});
  assert.deepEqual(gate.accept('보고서를 만들어 줘', true, NOW + 2000), {type:'no_wake'});
  assert.deepEqual(gate.accept('GPT야', false, NOW + 3000), {type:'rejected'});
});

test('fresh verified follow-up works but expiry and reset erase wake authorization', () => {
  const gate = new WakeGate();
  gate.accept('GPT야', true, NOW);
  assert.deepEqual(gate.accept('보고서를 만들어 줘', true, NOW + 1000), {type:'command', text:'보고서를 만들어 줘'});
  gate.accept('GPT야', true, NOW);
  assert.deepEqual(gate.accept('보고서를 만들어 줘', true, NOW + 15000), {type:'no_wake'});
  gate.accept('GPT야', true, NOW + 20000);
  gate.reset();
  assert.deepEqual(gate.accept('보고서를 만들어 줘', true, NOW + 20001), {type:'no_wake'});
});

test('long audio is rejected rather than being shortened into an accepted command', () => {
  assert.equal(signalQuality(audio(MAX_SECONDS)).ok, true);
  assert.equal(signalQuality(audio(MAX_SECONDS + .001)).reason, 'invalid_audio');
  const completed = [], rejected = [];
  const utterances = new Utterances(value => completed.push(value), () => {}, () => rejected.push(true));
  for (let index = 0; index < 160; index++) utterances.push(audio(.1), SAMPLE_RATE);
  assert.equal(rejected.length, 1);
  assert.equal(completed.length, 0);
  for (let index = 0; index < 9; index++) utterances.push(audio(.1, 0), SAMPLE_RATE);
  for (let index = 0; index < 5; index++) utterances.push(audio(.1), SAMPLE_RATE);
  for (let index = 0; index < 8; index++) utterances.push(audio(.1, 0), SAMPLE_RATE);
  assert.equal(completed.length, 1, 'new speech can resume only after a silence boundary');
});

test('utterance reset discards an unfinished command instead of carrying it into the next session', () => {
  const completed = [];
  const utterances = new Utterances(value => completed.push(value), () => {}, () => {});
  for (let index = 0; index < 8; index++) utterances.push(audio(.1), SAMPLE_RATE);
  utterances.reset();
  for (let index = 0; index < 8; index++) utterances.push(audio(.1, 0), SAMPLE_RATE);
  assert.equal(completed.length, 0);
});

test('high-risk phrases receive a nonauthorization warning rather than an execution grant', () => {
  assert.equal(riskHint('오늘 업무의 핵심을 정리해 줘'), '');
  for (const text of ['거래처에 송금해 줘', '배포해 줘', '게시해 줘', '목소리 권한을 바꿔 줘']) {
    assert.match(riskHint(text), /음성 확인은 실행 승인으로 사용하지 않습니다/);
  }
});

test('stopping during a pending microphone permission request also stops a late granted stream', async t => {
  let grantPermission;
  const track = {stopped:false, stop() { this.stopped=true; }};
  const stream = {getTracks:() => [track]};
  const connection = () => ({connect() {}, disconnect() {}});
  class AudioContextStub {
    constructor() { this.state='suspended'; this.sampleRate=48000; this.audioWorklet={addModule:async () => {}}; }
    createMediaStreamSource() { return connection(); }
    createGain() { return {...connection(), gain:{value:1}}; }
    async resume() { this.state='running'; }
    async close() { this.state='closed'; }
  }
  class AudioWorkletNodeStub {
    constructor() { this.port={onmessage:null}; }
    connect() {}
    disconnect() {}
  }
  for (const [key, value] of Object.entries({
    window:{isSecureContext:true},
    navigator:{mediaDevices:{getUserMedia:() => new Promise(resolve => { grantPermission=resolve; })}},
    AudioContext:AudioContextStub,
    AudioWorkletNode:AudioWorkletNodeStub,
  })) {
    const descriptor=Object.getOwnPropertyDescriptor(globalThis,key);
    Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});
    t.after(() => descriptor ? Object.defineProperty(globalThis,key,descriptor) : delete globalThis[key]);
  }
  const microphone=new Microphone();
  t.after(() => microphone.stop());
  const starting=microphone.start(() => {});
  await microphone.stop();
  grantPermission(stream);
  await starting.catch(() => {}); // Cancellation may resolve quietly or explicitly reject.
  assert.equal(track.stopped,true,'the stream granted after cancellation must be stopped');
  assert.equal(microphone.stream,null,'cancelled startup must not retain a microphone stream');
});
