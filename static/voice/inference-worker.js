// All inference runs in this module worker. Audio is never uploaded or persisted.
// Assets are pinned and served by AI Office; missing local assets fail closed.
import {
  AutoModel,
  AutoModelForAudioFrameClassification,
  AutoProcessor,
  env,
  pipeline,
} from '/voice/vendor/transformers.min.js';

const SAMPLE_RATE = 16000;
const SEGMENT_SAMPLES = SAMPLE_RATE * 10;
const SPEAKER_MODEL = 'onnx-community/wespeaker-voxceleb-resnet34-LM';
const SEGMENT_MODEL = 'onnx-community/pyannote-segmentation-3.0';
const ASR_MODEL = 'onnx-community/whisper-tiny';
const LABELS = [
  'NO_SPEAKER', 'SPEAKER_1', 'SPEAKER_2', 'SPEAKER_3',
  'SPEAKERS_1_AND_2', 'SPEAKERS_1_AND_3', 'SPEAKERS_2_AND_3',
];

env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = '/voice/models/';
env.backends.onnx.wasm.wasmPaths = '/voice/vendor/';
env.backends.onnx.wasm.numThreads = 1;
env.backends.onnx.wasm.proxy = false;

// Defense in depth: this worker can only fetch same-origin static model/runtime
// assets. Inference requests cannot send audio through fetch, even accidentally.
const assetFetch = globalThis.fetch.bind(globalThis);
globalThis.fetch = (input, init) => {
  const request = input instanceof Request ? input : null;
  const url = new URL(request ? request.url : String(input), self.location.href);
  const method = String(init?.method || request?.method || 'GET').toUpperCase();
  if (url.origin !== self.location.origin
      || !['GET', 'HEAD'].includes(method)
      || (init?.body != null)
      || (!url.pathname.startsWith('/voice/models/')
          && !url.pathname.startsWith('/voice/vendor/'))) {
    return Promise.reject(new Error('음성 Worker에서는 로컬 모델 파일만 읽을 수 있습니다.'));
  }
  return assetFetch(input, init);
};

let speakerPromise;
let segmentPromise;
let transcriberPromise;
let busy = false;

function validateAudio(audio, minimumSeconds, maximumSeconds = 12) {
  if (!(audio instanceof Float32Array)
      || audio.length < SAMPLE_RATE * minimumSeconds
      || audio.length > SAMPLE_RATE * maximumSeconds
      || (typeof SharedArrayBuffer !== 'undefined'
          && audio.buffer instanceof SharedArrayBuffer)) {
    throw new Error(`16kHz 모노 음성이 ${minimumSeconds}~${maximumSeconds}초 필요합니다.`);
  }
  let nonzero = false;
  for (const sample of audio) {
    if (!Number.isFinite(sample) || Math.abs(sample) > 1.0001) {
      throw new Error('음성 입력 범위가 올바르지 않습니다.');
    }
    nonzero ||= sample !== 0;
  }
  if (!nonzero) throw new Error('음성 입력이 비어 있습니다.');
  return audio;
}

async function loadSpeaker() {
  if (!speakerPromise) {
    speakerPromise = (async () => {
      const processor = await AutoProcessor.from_pretrained(SPEAKER_MODEL, {
        local_files_only: true,
      });
      const config = processor.feature_extractor?.config;
      if (config?.sampling_rate !== SAMPLE_RATE
          || config?.num_mel_bins !== 80
          || config?.feature_extractor_type !== 'WeSpeakerFeatureExtractor') {
        throw new Error('화자 모델의 전처리 설정이 올바르지 않습니다.');
      }
      const model = await AutoModel.from_pretrained(SPEAKER_MODEL, {
        device: 'wasm', dtype: 'q8', local_files_only: true,
      });
      return { processor, model };
    })().catch(error => {
      speakerPromise = undefined;
      throw error;
    });
  }
  return speakerPromise;
}

async function loadSegmenter() {
  if (!segmentPromise) {
    segmentPromise = (async () => {
      const processor = await AutoProcessor.from_pretrained(SEGMENT_MODEL, {
        local_files_only: true,
      });
      const feature = processor.feature_extractor;
      if (feature?.config?.sampling_rate !== SAMPLE_RATE
          || feature?.config?.offset !== 990
          || feature?.config?.step !== 270) {
        throw new Error('겹침 검사의 시간 설정이 올바르지 않습니다.');
      }
      const model = await AutoModelForAudioFrameClassification.from_pretrained(SEGMENT_MODEL, {
        device: 'wasm', dtype: 'fp32', local_files_only: true,
      });
      if (!LABELS.every((label, i) => model.config.id2label?.[i] === label)) {
        await model.dispose();
        throw new Error('겹침 검사의 화자 클래스가 올바르지 않습니다.');
      }
      return { processor, model };
    })().catch(error => {
      segmentPromise = undefined;
      throw error;
    });
  }
  return segmentPromise;
}

async function loadTranscriber() {
  if (!transcriberPromise) {
    transcriberPromise = pipeline('automatic-speech-recognition', ASR_MODEL, {
      device: 'wasm',
      dtype: { encoder_model: 'fp32', decoder_model_merged: 'q8' },
      local_files_only: true,
    }).catch(error => {
      transcriberPromise = undefined;
      throw error;
    });
  }
  return transcriberPromise;
}

function disposeTensors(container) {
  for (const value of Object.values(container || {})) value?.dispose?.();
}

async function embed(audio) {
  const { processor, model } = await loadSpeaker();
  let inputs;
  let outputs;
  try {
    inputs = await processor(audio);
    const feature = inputs.input_features;
    if (feature?.dims?.length !== 3 || feature.dims[0] !== 1 || feature.dims[2] !== 80) {
      throw new Error('화자 모델의 입력 특징이 올바르지 않습니다.');
    }
    outputs = await model(inputs);
    // The converted ONNX export must return one 256-dimensional embedding.
    // Reject an unexpected/multiple output instead of fabricating a voice score.
    const tensors = Object.values(outputs).filter(value => value?.dims && value?.data);
    if (tensors.length !== 1 || tensors[0].dims.length !== 2
        || tensors[0].dims[0] !== 1 || tensors[0].dims[1] !== 256) {
      throw new Error('화자 모델의 출력이 올바르지 않습니다.');
    }
    const values = Array.from(tensors[0].data);
    const norm = Math.sqrt(values.reduce((sum, value) => sum + value * value, 0));
    if (!values.every(Number.isFinite) || !Number.isFinite(norm) || norm < 1e-8) {
      throw new Error('화자 특징을 확인하지 못했습니다.');
    }
    return { embedding: values.map(value => value / norm) };
  } finally {
    disposeTensors(inputs);
    disposeTensors(outputs);
  }
}

function intervalSeconds(intervals) {
  if (!intervals.length) return 0;
  intervals.sort((a, b) => a[0] - b[0]);
  let [start, end] = intervals[0];
  let duration = 0;
  for (let i = 1; i < intervals.length; ++i) {
    const [nextStart, nextEnd] = intervals[i];
    if (nextStart <= end + 1e-8) end = Math.max(end, nextEnd);
    else {
      duration += end - start;
      [start, end] = [nextStart, nextEnd];
    }
  }
  return duration + end - start;
}

async function segment(audio) {
  const { processor, model } = await loadSegmenter();
  const duration = audio.length / SAMPLE_RATE;
  const starts = audio.length <= SEGMENT_SAMPLES
    ? [0] : [0, audio.length - SEGMENT_SAMPLES];
  const speechIntervals = [];
  const overlapIntervals = [];
  const uncertainIntervals = [];
  let overlapMax = 0;
  let speakerCount = 0;
  const frameSeconds = 10 / processor.feature_extractor.samples_to_frames(SEGMENT_SAMPLES);

  for (const startSample of starts) {
    const padded = new Float32Array(SEGMENT_SAMPLES);
    const available = Math.min(SEGMENT_SAMPLES, audio.length - startSample);
    padded.set(audio.subarray(startSample, startSample + available));
    let inputs;
    let outputs;
    try {
      inputs = await processor(padded);
      outputs = await model(inputs);
      const logits = outputs.logits;
      if (logits?.dims?.length !== 3 || logits.dims[0] !== 1 || logits.dims[2] !== 7
          || logits.dims[1] < 1 || logits.data.length !== logits.dims[1] * 7) {
        throw new Error('겹침 검사 결과를 확인하지 못했습니다.');
      }
      const slotSeconds = [0, 0, 0];
      for (let i = 0; i < logits.dims[1]; ++i) {
        const localStart = i * frameSeconds;
        if (localStart >= available / SAMPLE_RATE) break;
        const begin = startSample / SAMPLE_RATE + localStart;
        const end = Math.min(duration, startSample / SAMPLE_RATE + (i + 1) * frameSeconds);
        const row = Array.from(logits.data.subarray(i * 7, i * 7 + 7));
        if (!row.every(Number.isFinite)) throw new Error('겹침 검사 값이 올바르지 않습니다.');
        const maximum = Math.max(...row);
        const exps = row.map(value => Math.exp(value - maximum));
        const total = exps.reduce((sum, value) => sum + value, 0);
        const probabilities = exps.map(value => value / total);
        const overlap = probabilities[4] + probabilities[5] + probabilities[6];
        const confidence = Math.max(...probabilities);
        const label = probabilities.indexOf(confidence);
        overlapMax = Math.max(overlapMax, overlap);
        if (1 - probabilities[0] >= 0.5) speechIntervals.push([begin, end]);
        if (overlap >= 0.5) overlapIntervals.push([begin, end]);
        if (confidence < 0.6) uncertainIntervals.push([begin, end]);
        if (label >= 1 && label <= 3 && confidence >= 0.6) {
          slotSeconds[label - 1] += end - begin;
        }
      }
      // Labels are local to a 10-second chunk, so do not match slot IDs across
      // chunks. This only reports the largest within-chunk count.
      speakerCount = Math.max(speakerCount, slotSeconds.filter(seconds => seconds >= 0.3).length);
    } finally {
      disposeTensors(inputs);
      disposeTensors(outputs);
      padded.fill(0);
    }
  }
  const overlapSeconds = intervalSeconds(overlapIntervals);
  return {
    overlapMax,
    overlapSeconds,
    overlapProportion: overlapSeconds / duration,
    speechSeconds: intervalSeconds(speechIntervals),
    uncertainSeconds: intervalSeconds(uncertainIntervals),
    speakerCount,
  };
}

async function transcribe(audio, inline = false) {
  const transcriber = await loadTranscriber();
  const result = await transcriber(audio, {
    language: 'korean', task: 'transcribe',
    return_timestamps: false, max_new_tokens: inline ? 448 : 128,
    ...(inline ? {chunk_length_s: 20, stride_length_s: 3} : {}),
    do_sample: false,
  });
  if (Array.isArray(result) || typeof result?.text !== 'string' || result.text.length > 2000) {
    throw new Error('음성 인식 결과를 확인하지 못했습니다.');
  }
  const text = result.text.trim();
  if (!text) throw new Error('인식된 말이 없습니다.');
  return { text };
}

self.onmessage = async ({ data }) => {
  const id = data?.id;
  const audio = data?.audio;
  if (busy) {
    if (audio instanceof Float32Array && !(typeof SharedArrayBuffer !== 'undefined'
        && audio.buffer instanceof SharedArrayBuffer)) audio.fill(0);
    self.postMessage({ id, ok: false, error: '이전 음성을 처리 중입니다.' });
    return;
  }
  busy = true;
  try {
    if ((typeof id !== 'string' && typeof id !== 'number')
        || !['init', 'init-asr', 'embed', 'segment', 'transcribe', 'transcribe-inline'].includes(data?.type)) {
      throw new Error('지원하지 않는 음성 요청입니다.');
    }
    let result = {};
    if (data.type === 'init') {
      await loadSpeaker();
      await loadSegmenter();
    } else if (data.type === 'init-asr') {
      await loadTranscriber();
    } else {
      validateAudio(audio, data.type === 'embed' ? 0.75 : 0.25, data.type === 'transcribe-inline' ? 60 : 12);
      if (data.type === 'embed') result = await embed(audio);
      else if (data.type === 'segment') result = await segment(audio);
      else result = await transcribe(audio, data.type === 'transcribe-inline');
    }
    self.postMessage({ id, ok: true, ...result });
  } catch (error) {
    // Error messages contain no audio, embeddings, or transcription output.
    const message = error instanceof Error ? error.message : '로컬 음성 처리에 실패했습니다.';
    self.postMessage({ id, ok: false, error: message.slice(0, 300) });
  } finally {
    if (audio instanceof Float32Array && !(typeof SharedArrayBuffer !== 'undefined'
        && audio.buffer instanceof SharedArrayBuffer)) audio.fill(0);
    busy = false;
  }
};
