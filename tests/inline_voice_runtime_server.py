"""Manual browser check of the installed inline ASR engine, without a microphone.

Serve only local voice assets and one hash-checked, existing public WAV fixture.
No organization, database, employee, model download, or external HTTP client is
created. The browser performs inference only after the check button is clicked.
"""
import argparse
import hashlib
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
from unittest.mock import Mock
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from office.auth import OfficeAuth
from scripts.setup_voice_test_samples import FILES as PUBLIC_FIXTURE_HASHES
from server import handler_for


PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>인라인 음성 엔진 실검수</title>
<link rel="stylesheet" href="/voice/voice.css">
<script type="module" src="/inline-check.js"></script></head>
<body><main class="page"><section class="intro">
<p class="eyebrow">LOCAL RUNTIME CHECK</p><h1>인라인 음성 엔진 실검수</h1>
<p>설치된 실제 모델을 초기화하고 기존 공개 WAV 샘플 한 개를 전사합니다.
마이크·직원 실행·외부 전송은 사용하지 않습니다. 한국어 인식 정확도 평가는 아닙니다.</p>
</section><section class="setup-card">
<button class="primary" id="run-inline-check" type="button">실제 로컬 엔진 점검</button>
<p id="inline-check-status" role="status" aria-live="polite" data-state="idle">버튼을 누르면 점검을 시작합니다.</p>
<p id="inline-check-time">소요시간: 0.0초</p>
<pre id="inline-check-result"></pre>
</section></main></body></html>"""

SCRIPT = """import {VoiceEngine} from '/voice/rpc.mjs';
const button = document.querySelector('#run-inline-check');
const status = document.querySelector('#inline-check-status');
const output = document.querySelector('#inline-check-result');
const elapsed = document.querySelector('#inline-check-time');
const fixture = __FIXTURE_METADATA__;
button.addEventListener('click', async () => {
  button.disabled = true;
  output.textContent = '';
  status.dataset.state = 'running';
  const started = performance.now();
  const seconds = value => Math.round(value / 100) / 10;
  const tick = () => {elapsed.textContent = `소요시간: ${seconds(performance.now() - started).toFixed(1)}초`;};
  const timer = setInterval(tick, 250);
  let engine;
  let audio;
  let decoded;
  let phase = 'init-asr';
  try {
    status.textContent = '실제 로컬 ASR 모델을 불러오는 중…';
    engine = new VoiceEngine();
    const initStarted = performance.now();
    await engine.request('init-asr');
    const initSeconds = seconds(performance.now() - initStarted);
    phase = 'decode-public-fixture';
    status.textContent = '공개 샘플을 읽고 16kHz 모노 음성으로 준비하는 중…';
    const response = await fetch('/inline-check/sample.wav', {cache: 'no-store'});
    if (!response.ok) throw new Error(`공개 샘플을 읽지 못했습니다 (${response.status}).`);
    const context = new OfflineAudioContext(1, 16000, 16000);
    decoded = await context.decodeAudioData(await response.arrayBuffer());
    if (decoded.sampleRate !== 16000 || decoded.numberOfChannels !== 1)
      throw new Error('시험 샘플이 16kHz 모노로 디코딩되지 않았습니다.');
    audio = decoded.getChannelData(0).slice();
    const durationSeconds = audio.length / 16000;
    if (durationSeconds < 0.25 || durationSeconds > 60)
      throw new Error('시험 샘플 길이가 인라인 전사 범위를 벗어났습니다.');
    phase = 'transcribe-inline';
    status.textContent = '실제 transcribe-inline 호출을 처리하는 중…';
    const inferenceStarted = performance.now();
    const result = await engine.request('transcribe-inline', audio);
    if (typeof result.text !== 'string' || !result.text.trim())
      throw new Error('엔진에서 비어 있지 않은 전사문을 받지 못했습니다.');
    output.textContent = JSON.stringify({
      kind: 'real_public_fixture_inline_inference', passed: true,
      sample: fixture.name, sampleSha256: fixture.sha256,
      sampleSeconds: seconds(durationSeconds * 1000), sampleRate: 16000,
      calls: ['init-asr', 'transcribe-inline'], text: result.text,
      initSeconds, inferenceSeconds: seconds(performance.now() - inferenceStarted),
      elapsedSeconds: seconds(performance.now() - started),
      limitation: '공개 샘플 한 개의 실제 호출 확인이며, 한국어 정확도·마이크·휴대전화 검증은 아닙니다.'
    }, null, 2);
    status.dataset.state = 'passed';
    status.textContent = '실제 모델 초기화와 인라인 전사 호출을 완료했습니다.';
  } catch (error) {
    output.textContent = JSON.stringify({passed: false, phase,
      error: error instanceof Error ? error.message : String(error),
      elapsedSeconds: seconds(performance.now() - started)}, null, 2);
    status.dataset.state = 'failed';
    status.textContent = '점검 실패 · 아래 오류를 확인하세요.';
  } finally {
    audio?.fill(0);
    if (decoded) for (let i = 0; i < decoded.numberOfChannels; i++) decoded.getChannelData(i).fill(0);
    engine?.close();
    clearInterval(timer);
    tick();
    button.disabled = false;
  }
});
"""


def fixture_handler(sample_source):
    """Build an isolated handler; refuse arbitrary or changed recordings."""
    sample = Path(sample_source).resolve(strict=True)
    expected = PUBLIC_FIXTURE_HASHES.get(sample.name)
    if expected is None or not sample.is_file() or sample.stat().st_size > 1_000_000:
        raise ValueError("기존 공개 시험 WAV 4개 중 하나를 지정하세요.")
    data = sample.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("공개 시험 샘플의 해시가 일치하지 않습니다.")
    metadata = json.dumps({"name": sample.name, "sha256": expected}, ensure_ascii=False)
    script = SCRIPT.replace("__FIXTURE_METADATA__", metadata).encode("utf-8")
    auth = OfficeAuth()
    base = handler_for(Mock(), auth=auth)

    class InlineCheckHandler(base):
        def dispatch(self, write):
            try:
                auth.check_request(self.headers.get("Host", ""), self.headers.get("Origin"),
                                   self.client_address[0], self.server.server_port, write=write)
            except PermissionError as exc:
                self.respond(403, {"error": str(exc)})
                return
            if write:
                self.respond(405, {"error": "검증 서버는 파일 읽기만 허용합니다."})
                return
            path = urlsplit(self.path).path
            if path == "/inline-check":
                self.respond(200, PAGE.encode("utf-8"), "text/html; charset=utf-8", voice=True)
            elif path == "/inline-check.js":
                self.respond(200, script, "text/javascript; charset=utf-8", voice=True)
            elif path == "/inline-check/sample.wav":
                self.respond(200, data, "audio/wav", voice=True)
            elif path in ("/voice", "/voice/") or path.startswith("/voice/"):
                super().dispatch(False)
            else:
                self.respond(404, {"error": "검증 화면과 음성 정적 파일만 사용할 수 있습니다."})

    return InlineCheckHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-source", type=Path, required=True,
                        help="이미 설치된 공개 sv_speaker-*.wav 한 개의 경로")
    parser.add_argument("--port", type=int, default=8784)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        handler = fixture_handler(args.sample_source)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    server.daemon_threads = True
    print(f"Inline voice runtime check: http://127.0.0.1:{args.port}/inline-check", flush=True)
    print("공개 샘플만 사용 · 마이크/직원/DB/다운로드 없음 · 종료 Ctrl+C", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
