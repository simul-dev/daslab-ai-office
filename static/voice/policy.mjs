// Pure decision policy. Similarity is a model score, never a probability of identity.
export const MODEL_ID = 'onnx-community/wespeaker-voxceleb-resnet34-LM';
export const SAMPLE_RATE = 16000;
export const PROFILE_VERSION = 1;
export const MAX_SECONDS = 12;

export function normalise(vector) {
  if (!vector || vector.length !== 256 || Array.from(vector).some(v => !Number.isFinite(v))) throw new Error('유효한 목소리 특징이 없습니다.');
  const norm = Math.hypot(...vector);
  if (norm < 1e-8) throw new Error('목소리 특징을 계산하지 못했습니다.');
  return Array.from(vector, v => v / norm);
}
export function cosine(a, b) {
  a = normalise(a); b = normalise(b);
  return a.reduce((sum, v, i) => sum + v * b[i], 0);
}
export function readProfile(value) {
  if (!value || value.version !== PROFILE_VERSION || value.model !== MODEL_ID || value.embeddings?.length !== 3 ||
      !Number.isFinite(value.threshold) || value.threshold < .60 || value.threshold > .85 || !value.createdAt) return null;
  try { return {...value, embeddings: value.embeddings.map(normalise)}; } catch { return null; }
}
export function createProfile(embeddings, now = Date.now()) {
  if (embeddings.length !== 3) throw new Error('서로 다른 문장 3개를 등록해 주세요.');
  const normalised = embeddings.map(normalise);
  const similarities = [cosine(normalised[0], normalised[1]), cosine(normalised[0], normalised[2]), cosine(normalised[1], normalised[2])];
  const minimum = Math.min(...similarities);
  if (minimum < .70) throw new Error('세 녹음의 목소리가 충분히 일치하지 않습니다. 조용한 곳에서 다시 등록해 주세요.');
  return {version: PROFILE_VERSION, model: MODEL_ID, embeddings: normalised,
    threshold: Math.max(.60, Math.min(.85, minimum - .10)), createdAt: new Date(now).toISOString()};
}
export function signalQuality(audio) {
  if (!(audio instanceof Float32Array) || !audio.length || audio.length > SAMPLE_RATE * MAX_SECONDS || audio.some(v => !Number.isFinite(v) || Math.abs(v)>1.0001)) return {ok:false, reason:'invalid_audio'};
  let energy = 0, clipped = 0;
  for (const v of audio) { energy += v * v; if (Math.abs(v) >= .995) clipped++; }
  const rms = Math.sqrt(energy / audio.length);
  if (rms < .003) return {ok:false, reason:'quiet_audio'};
  if (clipped / audio.length > .015) return {ok:false, reason:'clipped_audio'};
  return {ok:true, seconds:audio.length / SAMPLE_RATE, rms};
}
export function assessSegment(segment, minimumSpeech = 1.2) {
  if (!segment || ![segment.overlapMax, segment.overlapSeconds, segment.speechSeconds, segment.uncertainSeconds].every(v=>Number.isFinite(v)&&v>=0)
      || segment.overlapMax>1 || !Number.isInteger(segment.speakerCount) || segment.speakerCount<1) return {ok:false, reason:'unknown_speech'};
  if (segment.overlapSeconds >= .08 || segment.overlapMax >= .65) return {ok:false, reason:'overlap'};
  if (segment.speakerCount > 1) return {ok:false, reason:'speaker_change'};
  if (segment.speechSeconds < minimumSpeech) return {ok:false, reason:'short_speech'};
  if (segment.uncertainSeconds > .25) return {ok:false, reason:'uncertain_speech'};
  return {ok:true};
}
export function verifyEmbeddings(profile, embeddings) {
  profile = readProfile(profile);
  if (!profile || !embeddings?.length) return {ok:false, reason:'not_enrolled'};
  try {
    const centroid = normalise(profile.embeddings[0].map((_, i) => profile.embeddings.reduce((sum, v) => sum + v[i], 0) / 3));
    const scores = embeddings.map(e => cosine(e, centroid));
    const score = Math.min(...scores);
    return {ok:score >= profile.threshold, reason:score >= profile.threshold ? 'matched' : 'different_voice', score, threshold:profile.threshold};
  } catch { return {ok:false, reason:'unknown_voice'}; }
}
export function verificationWindows(audio) {
  // Every part of the command is covered, not just its wake word or global mean.
  if (audio.length <= 3 * SAMPLE_RATE) return [audio.slice()];
  const width = 3 * SAMPLE_RATE, stride = 2 * SAMPLE_RATE, result = [];
  for (let start = 0; start + width <= audio.length; start += stride) result.push(audio.slice(start, start + width));
  if ((audio.length - width) % stride !== 0) result.push(audio.slice(-width));
  return result;
}
export function parseWake(text) {
  if (typeof text !== 'string') return null;
  const cleaned = text.trim().replace(/^[\s.,!?。…]+/u, '');
  const match = cleaned.match(/^(?:(?:g\s*p\s*t)|(?:지\s*피\s*티))\s*야(?:[\s,.!?。…:，]*)/iu);
  return match ? {command:cleaned.slice(match[0].length).trim()} : null;
}
export class WakeGate {
  constructor() { this.reset(); }
  reset() { this.armedUntil = 0; }
  accept(text, verified, now = Date.now()) {
    if (!verified) { this.reset(); return {type:'rejected'}; }
    const wake = parseWake(text);
    if (wake) {
      if (wake.command.length >= 2) { this.reset(); return {type:'command', text:wake.command}; }
      this.armedUntil = now + 15000;
      return {type:'wake'};
    }
    if (now < this.armedUntil && text?.trim().length >= 2) { this.reset(); return {type:'command', text:text.trim()}; }
    this.reset(); return {type:'no_wake'};
  }
}
export function riskHint(text) {
  return /결제|송금|구매|계약|서명|배포|삭제|권한|비밀번호|발송|게시|투자|입찰.*제출/u.test(text) ?
    '이 요청은 중요한 실행을 포함할 수 있습니다. 음성 확인은 실행 승인으로 사용하지 않습니다. 현재 PM은 검토·초안까지만 처리합니다.' : '';
}
