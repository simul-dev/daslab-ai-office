# 기기 내 음성 엔진과 설치 기록

이 구성은 대표가 명시적으로 듣기를 켠 **PC 전경 웹 시제품**의 로컬 음성 추론에 사용한다. 음성 전사, 등록한 화자와의 유사도, 중복 발화 검사를 브라우저의 Worker/WASM에서 수행한다. 한국어 호출어 `GPT야`는 전사 결과에서 확인하는 방식이며, 전용 저전력 호출어 칩이나 신원 인증 장치가 아니다.

**모델 파일 준비와 실제 인식 품질은 다른 상태다.** 설치기가 만드는 `assets-status.json`의 `ready: true`는 모든 필수 파일의 크기와 SHA256이 일치한다는 뜻이다. 대표 음성·다른 사람·동시 발화·한국어 명령에 대한 실제 FAR(타인 오수락률), FRR(대표 오거절률), 응답 지연은 아직 검증되지 않았다. 파일 준비나 코드 테스트 통과를 이 지표의 검증으로 표현하지 않는다.

## 고정 모델과 역할

| 역할 | 모델과 고정 버전 | 실제 설치 구성 | 모델 파일 합계 |
| --- | --- | --- | ---: |
| 등록 화자 비교 | [WeSpeaker VoxCeleb ResNet34 LM](https://huggingface.co/onnx-community/wespeaker-voxceleb-resnet34-LM/tree/6a61a1833ff2583aabeba044f5c8221f00b67ceb) | `model_quantized.onnx` q8, config, preprocessor, 변환 모델 카드 | 6,685,573 bytes |
| 한국어 호출어·명령 전사 | [Whisper tiny 다국어](https://huggingface.co/onnx-community/whisper-tiny/tree/ff4177021cc41f7db950912b73ea4fdf7d01d8e7) | fp32 encoder, q8 merged decoder, config·generation·processor·tokenizer 파일 | 68,014,184 bytes |
| 발화 구간·화자 전환·겹침 검사 | [pyannote segmentation 3.0](https://huggingface.co/onnx-community/pyannote-segmentation-3.0/tree/733a93b6473d019a773298e08cefa686894b1854) | `model.onnx` fp32, config, preprocessor, 변환 모델 카드 | 5,991,892 bytes |

위 크기는 해당 모델 저장소에서 선택한 파일 합계이며, 별도로 보존하는 라이선스·원본 모델 카드와 JS/WASM은 제외한다. 전체 필수 자산은 **32개, 103,584,791 bytes(약 103.6 MB)**다. 브라우저 실행 메모리와 초기 다운로드 압축 크기는 이 숫자와 다르다.

Whisper는 `.en`이 없는 다국어 모델이다. 공식 문서가 Whisper encoder의 양자화 민감성을 명시해 fp32 encoder를 선택했다. 이는 사용자 음성 정확도의 보장이 아니라 초기 정밀도 선택이다. [Transformers.js 양자화 문서](https://huggingface.co/docs/transformers.js/v3.8.1/en/guides/dtypes)

WeSpeaker 임베딩의 코사인 유사도는 사람의 신원을 보증하지 않으며, 녹음 재생·합성 음성을 차단하는 생체활성 검사가 아니다. pyannote의 화자 번호도 짧은 오디오 구간 안의 번호이며 등록된 대표 신원을 뜻하지 않는다. 모델은 16 kHz mono 입력의 10초 구간에서 비음성·단일 화자·동시 화자에 해당하는 7개 클래스를 출력한다. 화자 확인과 겹침 판정을 별도로 결합해야 한다. [원본 WeSpeaker 모델](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM), [원본 segmentation 모델](https://huggingface.co/pyannote/segmentation-3.0)

## 설치와 무결성 검사

프로젝트 루트에서 실행한다.

```powershell
npm ci --ignore-scripts
python scripts/setup_voice_assets.py
python scripts/setup_voice_assets.py --check
```

- npm은 `package-lock.json`으로 고정된 런타임을 설치한다. 설치 스크립트나 유료 API를 실행하지 않는다.
- Python 설치기는 `config/voice-assets.json`만 읽으며 표준 라이브러리만 사용한다. 자동 최신 버전 검색, 모델 변환, 학습, Hugging Face 토큰 입력은 없다.
- JS/WASM과 라이선스를 설치된 npm 패키지에서 복사한다. 원본 크기·SHA256도 manifest와 대조하므로 다른 버전이나 다른 빌드를 조용히 복사하지 않는다.
- 모델은 manifest의 전체 commit SHA로 고정된 공개 Hugging Face URL에서 받는다. 작은 설정 파일도 예외 없이 고정 크기·SHA256을 검사한다. Git LFS의 작은 포인터 파일을 가중치로 오인하지 않는다.
- 이미 크기·해시가 맞는 파일은 건너뛴다. 누락되거나 틀린 파일만 다시 받는다. 네트워크 오류는 기본 최대 3회 시도하며 `--retries 1`로 줄일 수 있다.
- 새 내용은 대상 파일과 같은 폴더의 임시 파일에 기록한다. 전체 크기·해시 확인과 디스크 flush를 마친 뒤 `os.replace`로 파일 하나씩 교체한다. 실패한 다운로드로 기존 파일을 덮어쓰지 않는다. 경로 이탈과 기존 symlink/junction을 통한 출력 디렉터리 이탈도 거절한다.
- 설치 시작에는 `ready: false`, 완료 후 전체 재검증이 통과해야 `ready: true`를 기록한다. 오류·중단은 실패 상태를 남긴다. `--check`는 **네트워크·파일 복사·상태 파일 변경이 없는 읽기 전용 검사**이며 전체 상태를 stdout에 출력한다. 성공 exit code 0, 누락·불일치 1이다.

런타임의 `transformers.min.js`는 ONNX Runtime을 포함한 브라우저 번들이다. 외부 bare import를 요구할 수 있는 `transformers.web.min.js`로 임의 교체하지 않는다. 같은 배포물의 `ort-wasm-simd-threaded.jsep.mjs`와 `.wasm`을 함께 제공한다. [공식 빌드 설정](https://github.com/huggingface/transformers.js/blob/3.8.1/webpack.config.js), [ONNX Runtime 버전 일치 요구](https://onnxruntime.ai/docs/tutorials/web/env-flags-and-session-options.html)

## 배치 경로와 브라우저 설정

```text
static/voice/vendor/transformers.min.js
static/voice/vendor/ort-wasm-simd-threaded.jsep.mjs
static/voice/vendor/ort-wasm-simd-threaded.jsep.wasm
static/voice/vendor/licenses/...
static/voice/models/onnx-community/wespeaker-voxceleb-resnet34-LM/...
static/voice/models/onnx-community/whisper-tiny/...
static/voice/models/onnx-community/pyannote-segmentation-3.0/...
static/voice/assets-status.json
```

서버가 `/static/` 경로로 위 폴더를 제공할 때 Worker의 공통 설정은 다음과 같다. 모델 ID는 manifest의 `repo`와 동일하게 유지한다.

```js
import { env, pipeline } from "/static/voice/vendor/transformers.min.js";

env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = "/static/voice/models/";
env.backends.onnx.wasm.wasmPaths =
  new URL("/static/voice/vendor/", self.location.origin).href;
env.backends.onnx.wasm.numThreads = 1;
env.backends.onnx.wasm.proxy = false;

const transcribe = await pipeline(
  "automatic-speech-recognition",
  "onnx-community/whisper-tiny",
  { device: "wasm", dtype: {
    encoder_model: "fp32", decoder_model_merged: "q8",
  } },
);
const result = await transcribe(pcm16kMono, {
  language: "korean", task: "transcribe", return_timestamps: false,
});
```

`pcm16kMono`는 실제로 16 kHz mono로 리샘플링한 `Float32Array`여야 한다. 마이크의 희망 sampleRate만 지정하고 변환을 생략하지 않는다. 원시 배열 입력에는 Transformers.js가 주파수 검사를 하지 않는다. 별도의 전용 Worker를 사용하므로 초기 WASM은 1 thread로 구성하며, 다중 스레드 최적화는 cross-origin isolation과 실제 성능을 확인한 뒤 적용한다. [입력 API](https://github.com/huggingface/transformers.js/blob/3.8.1/src/pipelines.js), [로컬 모델 설정](https://huggingface.co/docs/transformers.js/v3.8.1/en/custom_usage), [WASM thread 설정](https://onnxruntime.ai/docs/tutorials/web/env-flags-and-session-options.html)

## 개인정보와 동작 경계

- 설치 단계에서만 공개 모델·라이브러리 다운로드를 위해 인터넷을 쓴다. 실행 단계의 모델·WASM 요청은 위 same-origin 정적 파일로 제한한다. 브라우저에 캐시됐다는 이유만으로 외부 모델 URL을 허용하지 않는다.
- 원음은 기기 메모리에서 처리하고 서버 업로드·서버 녹음·학습용 저장을 하지 않는 것이 구현 계약이다. 이 설치기에는 마이크 접근·음성 파일 입력·오디오 업로드 기능이 없다. 명령 실행에 필요한 텍스트와 원음은 구분한다.
- Web Speech의 `SpeechRecognition`/`webkitSpeechRecognition`나 클라우드 전사를 오류 대체 경로로 사용하지 않는다. 로컬 모델 실패 시 음성 기능을 중단하고 텍스트 입력을 유지한다. 유료 추론 API 전환은 없다.
- 타인 발화 또는 주변 동시 발화를 감지하면 명령을 거절한다. 겹침 여부·등록 화자 판단이 불확실한 오디오를 실행 명령으로 승격시키지 않는다. 다른 사람이 끼어든 전체 구간을 단일 대표 발화로 평균내어 통과시키지 않는다.
- 실제 FAR/FRR·한국어 호출어 성공률·동시 발화 누락률은 아직 미측정이다. 녹음 재생·스피커 출력·TV·소음·거리·기기 차이에 대한 시험 없이 화자 확인을 보안 인증으로 사용하지 않는다.

PC의 localhost는 마이크 권한을 요청할 수 있지만, 휴대폰이 PC의 `http://192.168...` 주소로 접근하는 것은 localhost 예외가 아니다. Fold4에서 마이크를 사용하려면 HTTPS와 사용자 권한이 필요하다. 현재 Fold4의 실제 지연·정확도·발열은 검증하지 않았다. [MDN getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)

Screen Wake Lock은 보이는 웹 화면이 꺼지는 것을 막는 기능이고, 숨겨진 탭이나 잠금화면에서의 상시 청취를 보장하지 않는다. 화면 잠금·다른 앱 전환 시 브라우저 작업은 중단·동결될 수 있다. 잠금화면 청취가 필수인 버전은 네이티브 Android microphone foreground service를 별도로 설계해야 한다. [MDN Wake Lock](https://developer.mozilla.org/en-US/docs/Web/API/Screen_Wake_Lock_API), [Chrome Page Lifecycle](https://developer.chrome.com/docs/web-platform/page-lifecycle-api), [Android microphone service](https://developer.android.com/develop/background-work/services/fgs/service-types#microphone)

## 라이선스와 출처

모델과 라이브러리의 라이선스를 혼동하지 않는다. manifest가 다운로드·복사하는 notice 파일도 크기·SHA256을 검사한다.

| 구성 | 라이선스·표기 | 함께 보존하는 자료 |
| --- | --- | --- |
| Transformers.js 3.8.1, Hugging Face | Apache-2.0 | npm `LICENSE` → `vendor/licenses/transformers-LICENSE` |
| Hugging Face Jinja | MIT | npm `LICENSE` → `vendor/licenses/jinja-LICENSE` |
| ONNX Runtime Web, Microsoft | MIT 및 제3자 notices | 실제 npm runtime commit `89f8206ba4f1c22c39e0297fb55272e8ce8cd7d0`의 `LICENSE`, `ThirdPartyNotices.txt` |
| Whisper, OpenAI; ONNX conversion, ONNX Community | MIT | 고정 원본 Git commit의 LICENSE와 고정 ONNX 모델 카드 |
| WeSpeaker VoxCeleb ResNet34 LM; WeSpeaker 저자, pyannote wrapper, ONNX Community 변환 | **CC BY 4.0 가중치** | 원본 모델 카드(저자·논문·출처), 변환 모델 카드, CC BY 4.0 legalcode |
| pyannote segmentation 3.0; Séverin Baroudi, Alexis Plaquet, Hervé Bredin; ONNX Community 변환 | MIT | 변환 모델 카드의 MIT 표기·원본 연결, pyannote.audio 3.0.0 소스 commit의 MIT LICENSE |

WeSpeaker 출처 표기는 [원본 모델 카드](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM)와 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)를 따른다. 이 배포는 원본을 ONNX로 변환한 커뮤니티 버전에서 q8 양자화 파일을 선택했음을 밝힌다. 해당 저자들이 DAS Lab 제품을 보증한다는 의미는 없다.

pyannote 원본 모델의 PyTorch 다운로드에는 Hugging Face 접근 동의가 안내되어 있다. 이 설치기는 별도로 공개 배포된 `onnx-community/pyannote-segmentation-3.0`의 고정 ONNX 파일만 사용하며, 원본 계정·토큰·유료 pyannoteAI 서비스를 호출하지 않는다. 보존하는 pyannote LICENSE 파일은 원본 라이브러리의 저작권 고지를 위한 것으로, 가중치의 MIT 표기는 원본·변환 모델 카드에서 따로 확인한 내용이다.

## 이 변경에서 확인한 범위

공식 Hugging Face API의 고정 revision 파일 목록·LFS SHA256과 작은 설정 파일의 실제 SHA256을 확인했다. 설치된 npm 브라우저 자산 5개의 실제 크기·해시가 manifest와 일치한다. 설치기에서 정상 원자 교체, 잘린/커진/해시가 틀린 입력 거절, 기존 파일 보존, 임시 파일 정리, 경로 이탈 거절, 실패 상태 생성도 로컬 임시 디렉터리로 확인했다.

이 기록 자체는 모델 전체 다운로드 성공, 브라우저 추론 성공, 화자·동시 발화 거절 성능, Fold4 실기기 검증을 뜻하지 않는다. 해당 검증은 실제 실행 결과에 따라 별도 기록한다.
