# 로컬 음성 모델 선정 검토

검토일: **2026-09-24**. 범위: 소유자의 짧은 한국어 호출·명령, 주변 음성의 외부 전송 금지, 브라우저와 Galaxy Z Fold4 내 연산. 공식 모델 카드·논문·저장소·배포 API를 읽은 기술 선정 기록이다. 이 문서 작성 중에는 모델을 교체하지 않았다.

**현재 WeSpeaker ResNet34-LM + pyannote segmentation-3.0 + Whisper tiny를 최신 SOTA 또는 검증된 최선이라고 부르면 안 된다.** 현재 조합은 작은 공개 ONNX 자산과 이미 지원되는 브라우저 API를 사용한 개발 기준선이다. 더 새로운 후보가 확인됐고, 후보 간 한국어·Fold4 실험은 아직 하지 않았다. 문서 조사와 실제 제품 성능 검증을 구분한다.

## 이번 경계에서의 결정

- **현재 브라우저 구현을 비교 기준선으로 유지한다.** 모델을 로컬에서 실행하고 실패하면 전송을 막는 경로를 먼저 실측한다. 이 선택은 구현 가능성·자산 크기·전처리 재현성에 근거하며 정확도 우승을 뜻하지 않는다.
- **다음 Android native 검토는 sherpa-onnx + CAM++를 경량 비교 대상으로, ERes2NetV2를 짧은 발화 정확도 비교 대상으로 시작한다.** ReDimNet2 B0/B1도 최신 소형 후보로 함께 평가하되 ONNX 변환·전처리 동등성부터 확인한다. 장치 시험 전 최종 우승 모델을 정하지 않는다.
- **Community-1 전체 파이프라인을 넣는 것이 겹침 거절을 자동 개선하지는 않는다.** 장시간 화자 추적과 단일 명령의 겹침 검사는 목적이 다르다. Precision-2의 표준 클라우드 경로는 주변 원음을 기기 밖으로 보내지 않는 이번 조건에 맞지 않는다.

## 화자 확인 후보

| 후보와 확인된 시점 | 공식 자료에서 확인한 장점·제약 | 이번 판단 |
| --- | --- | --- |
| **WeSpeaker ResNet34-LM**. 원모델 저장소 생성 2024-02-26, 마지막 수정 2024-05-06. 이는 저장소 기록일이지 학습일은 아니다. | VoxCeleb2 기반 256차원 임베딩. 현재 ONNX 변환본 q8은 약 6.69 MB. Transformers.js에 16kHz·80-bin FBANK·평균 제거 전처리가 구현돼 있다. LM은 긴 음성에서의 성능 향상을 목표로 하며 짧은 한국어 호출의 성능 증거가 아니다. [원모델](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM), [공식 사전학습 설명](https://github.com/wenet-e2e/wespeaker/blob/master/docs/pretrained.md), [변환 파일](https://huggingface.co/onnx-community/wespeaker-voxceleb-resnet34-LM/tree/main/onnx) | 브라우저 기준선. 0.75초 입력을 계산할 수 있다는 사실과 그 길이에서 신뢰할 수 있다는 주장을 구분한다. |
| **3D-Speaker CAM++**. 논문 최초 2023-03-01. | 공개 학습법·공식 ONNX export·C++ 전처리 경로와 sherpa Android 예제가 있다. 200k 화자 모델 카드는 중국어 모델로 표시한다. 중국어 학습이 한국어에 더 좋다고 추정하지 않는다. [논문](https://arxiv.org/abs/2303.00332), [공식 저장소](https://github.com/modelscope/3D-Speaker), [ONNX 실행 경로](https://github.com/modelscope/3D-Speaker/tree/main/runtime/onnxruntime) | native에서 도입 부담이 작은 경량 비교 후보. 동일 한국어 녹음으로 WeSpeaker와 비교한다. |
| **3D-Speaker ERes2NetV2**. 논문 2024-06-04, 200k 화자 가중치 공개 소식 2024-08. | 짧은 발화를 직접 연구한다. 논문의 같은 VoxCeleb1-O 조건에서도 전체/3초/2초 EER가 각각 0.61/0.98/1.48%로 달라진다. 이 수치를 우리 제품의 오수락률이나 한국어 정확도로 사용하면 안 된다. 약 17.8M 파라미터로 CAM++보다 큰 후보이며 공식 ONNX export 경로가 있다. [논문](https://arxiv.org/abs/2406.02167), [공식 학습·모델 안내](https://github.com/modelscope/3D-Speaker/blob/main/egs/voxceleb/sv-eres2netv2/README.md) | 2~3초 한국어 검증의 우선 비교 후보. Fold4 지연·발열은 미측정. 일반 ERes2Net Android 예제를 V2 지원 완료로 오인하지 않는다. |
| **ReDimNet / ReDimNet2**. 원 논문 2024-07-25, 후속 논문 2026-03-12. | ReDimNet2는 B0~B6와 공개 가중치를 제공한다. B0 1.1M/B1 2.1M 등 작은 구성이 있어 휴대기기 후보 가치가 있다. 공식 사용법은 PyTorch이며 이번 조사에서 공식 브라우저 패키지나 sherpa의 ReDimNet2 통합을 확인하지 못했다. [원 저장소](https://github.com/IDRnD/redimnet), [후속 논문](https://arxiv.org/abs/2603.11841), [후속 코드·가중치](https://github.com/PalabraAI/redimnet2) | 현재 조사 후보 중 2026년 후속 연구가 확인된 계열. 최신이라는 이유만으로 교체하지 않고 B0/B1 변환·한국어 시험을 추가한다. |
| **WavLM Base Plus SV**. 기반 논문 최초 2021-10-26, 개정 2022-06-17. | self-supervised 표현을 화자 확인에 미세조정한 모델. 공식 예제도 최적 임계값이 데이터에 의존한다고 명시한다. JS용 변환본은 fp32 약 402 MB/q8 약 102 MB로 현재 WeSpeaker보다 크다. [원모델](https://huggingface.co/microsoft/wavlm-base-plus-sv), [논문](https://arxiv.org/abs/2110.13900), [JS 변환 파일](https://huggingface.co/Xenova/wavlm-base-plus-sv/tree/main/onnx) | 정확도 비교는 가능하지만 소형 브라우저의 첫 후보로 선정할 근거는 부족하다. 겹침을 사용한 학습이 겹친 두 목소리의 소유자 판정을 보장하지 않는다. |
| **SpeechBrain ECAPA-TDNN**. 구조 논문 2020-05-14, 모델 카드의 성능 기록은 2021년. | 16kHz 단일 채널, VoxCeleb1+2 학습, cosine 비교를 제공한다. 공식 카드가 다른 데이터셋 성능을 보장하지 않는다고 명시한다. 공식 배포 경로는 SpeechBrain/PyTorch이며 이번에 브라우저 변환본을 시험하지 않았다. [논문](https://arxiv.org/abs/2005.07143), [모델 카드](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) | 오래된 유효 기준선 후보. ECAPA라는 이름만으로 현재 모델보다 최신·우수하다고 판단하지 않는다. |

ReDimNet2 날짜는 서로 다른 기록을 합치지 않았다. 논문 접수일은 2026-03-12, 원 ReDimNet README의 후속 출시 소식은 2026-07-01, GitHub `v1.0.0` release API의 `published_at`은 2026-03-04다. 이 차이를 확인하지 않고 하나의 최초 출시일로 단정하지 않는다. [release 기록](https://github.com/PalabraAI/redimnet2/releases/tag/v1.0.0)

## 겹침·화자 분할 후보

| 후보 | 실제 범위와 로컬 적합성 | 판단 |
| --- | --- | --- |
| **pyannote segmentation-3.0** | pyannote.audio 3.0 release는 2023-09-26. 10초·16kHz mono 입력, 무음/단일 화자 3개/화자 쌍 3개인 7-class powerset 출력. 현재 변환본 fp32 약 5.99 MB. 모델 하나가 전체 녹음의 일관된 화자 신원을 추적하지는 않는다. [공식 모델](https://huggingface.co/pyannote/segmentation-3.0), [브라우저 변환·API](https://huggingface.co/onnx-community/pyannote-segmentation-3.0), [3.0 release](https://github.com/pyannote/pyannote-audio/releases/tag/3.0.0) | 소유자 확인 전 겹침·불확실 구간을 거절하는 작은 로컬 검사 기준선. 확률 합산 임계값은 제품 데이터로 검증해야 한다. |
| **Community-1** | pyannote.audio 4.0과 함께 공개; 4.0 release는 2025-09-29. 로컬 Python 실행·오프라인 사용 가능. 공식 설명상 개선 중심은 화자 배정·개수이며 VAD/겹침 segmentation 성능은 3.1과 같다. `exclusive` 결과는 겹침을 없앤 출력 표현이므로 겹침이 없었다는 증거가 아니다. [모델](https://huggingface.co/pyannote/speaker-diarization-community-1), [출시 설명](https://www.pyannote.ai/blog/community-1), [4.0 release](https://github.com/pyannote/pyannote-audio/releases/tag/4.0.0) | 긴 대화의 화자 추적에는 검토 가치. 현재 작은 거절 검사에 전체 파이프라인을 넣을 이득·브라우저 비용은 미검증. |
| **Precision-2** | 공식 문서는 더 나은 diarization/겹침 벤치를 제시하지만 표준 API와 Python 래퍼는 pyannoteAI 서버에서 실행한다. 공개 브라우저 가중치를 이번에 확인하지 못했다. 원문 페이지에서 정확한 출시일은 확인하지 못했으므로 날짜를 만들어 넣지 않는다. [공식 설명](https://www.pyannote.ai/blog/precision-2), [실행 위치 안내](https://huggingface.co/pyannote/speaker-diarization-community-1) | 현 local-only 경로에서 제외. 벤치 우위로 원음 외부전송 조건을 바꾸지 않는다. |
| **diart** | overlap-aware 온라인 segmentation+embedding+incremental clustering을 묶는 Python 프레임워크. 단일 신형 화자 모델이 아니다. 기본 모델 접근조건과 런타임 의존성이 따로 있다. [공식 구현](https://github.com/juanmc2005/diart) | native/로컬 Python의 지속 대화 연구 후보. WebSocket 서버 예제를 사용하면 브라우저 원음이 서버로 이동하므로 이번 구현에 그대로 적용할 수 없다. |

## 코드와 가중치의 라이선스

아래는 읽은 배포자의 표시다. 코드 라이선스를 모든 가중치에 자동 적용하지 않는다. 모델을 배포할 때 버전·해시·출처·해당 라이선스를 함께 보존한다.

| 대상 | 코드·엔진 | 선택 가중치 또는 공식 표시 |
| --- | --- | --- |
| WeSpeaker | Apache-2.0 | 원모델·ONNX 변환본 모두 CC-BY-4.0. [원모델 카드](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM) |
| CAM++ / ERes2NetV2 | 3D-Speaker Apache-2.0 | 공식 ModelScope README의 `license: Apache License 2.0`을 직접 확인. CAM++ widget revision v1.0.0, ERes2NetV2 v1.0.2는 카드의 예제 revision이며 최신 가중치 해시를 뜻하지 않는다. [CAM++ 원문](https://modelscope.cn/api/v1/models/iic/speech_campplus_sv_zh-cn_16k-common/repo?Revision=master&FilePath=README.md), [ERes2NetV2 원문](https://modelscope.cn/api/v1/models/iic/speech_eres2netv2_sv_zh-cn_16k-common/repo?Revision=master&FilePath=README.md) |
| ReDimNet / ReDimNet2 | 공식 저장소 MIT | 같은 공식 저장소에 가중치를 배포하지만 별도 weight license 문서는 이번에 확인하지 못했다. 실제 채택할 release의 고지를 함께 보존할 필요가 있다. [ReDimNet2](https://github.com/PalabraAI/redimnet2) |
| Microsoft WavLM Base Plus SV | 사용할 추론 엔진의 라이선스 별도 | 공식 모델 카드의 License 링크가 **UniSpeech의 CC-BY-SA-3.0**으로 연결됨을 확인. 다른 Microsoft 저장소의 MIT 표시로 덮어쓰면 안 된다. [공식 연결 LICENSE](https://github.com/microsoft/UniSpeech/blob/main/LICENSE) |
| SpeechBrain ECAPA | SpeechBrain Apache-2.0 | 선택 모델 카드 Apache-2.0. [모델](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) |
| pyannote / diart | pyannote.audio·diart MIT | segmentation-3.0 및 선택 ONNX 변환본 MIT; Community-1 CC-BY-4.0; Precision-2는 상용 서비스 경로로 공개 weight license를 확인하지 못함. [segmentation](https://huggingface.co/pyannote/segmentation-3.0), [Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) |
| sherpa-onnx | Apache-2.0 | 모델별로 원가중치 라이선스를 확인해야 한다고 공식 Android 안내가 명시. [Android 목록](https://k2-fsa.github.io/sherpa/onnx/speaker-identification/apk.html) |

## Whisper와 기기 실행 판단

Whisper tiny는 39M 파라미터의 경량 다국어 모델이다. 원 논문은 2022년이며 공식 저장소에는 large-v3 계열과 turbo도 있다. 따라서 tiny를 최신 또는 한국어 최고 정확도라고 소개하지 않는다. 더 큰 모델의 GPU 처리량 표를 Fold4 브라우저 속도로 옮겨 적어서도 안 된다. 현재 한국어 강제 지정, 호출어 보존, 무음 환각·단어 누락·명령 오류를 따로 검사한다. 이 문서는 최신 한국어 ASR 전체의 순위 비교까지 완료하지 않았다. [Whisper 논문](https://arxiv.org/abs/2212.04356), [공식 모델 목록·코드/가중치 MIT 표시](https://github.com/openai/whisper)

브라우저 WASM은 원음을 기기 내에서 계산하는 실행 방법이다. WebGPU보다 연산자 호환 범위가 넓지만 모든 기기에서 빠르다는 보장은 없다. 모델 파일 크기는 실제 최고 메모리 사용량과 다르다. 현재 한 스레드 설정의 시작시간·명령 지연·메모리·발열·배터리는 **실제 Fold4에서 미측정**이다. [ONNX Runtime Web 공식 안내](https://onnxruntime.ai/docs/tutorials/web/)

sherpa-onnx는 arm64-v8a 화자 확인 APK와 CAM++/WeSpeaker 등 모델 예제를 제공한다. 이는 Android 진입 경로의 증거이며 Fold4의 한국어 인식 성공 증거는 아니다. native로 옮기면 마이크 foreground service, 권한, 재시작·화면 꺼짐 상태를 별도로 설계한다. Android는 microphone service의 백그라운드 시작에 제한을 두므로 모델 교체만으로 상시 청취가 완성되지 않는다. [sherpa APK 목록](https://k2-fsa.github.io/sherpa/onnx/speaker-identification/apk.html), [Android microphone service](https://developer.android.com/develop/background-work/services/fgs/service-types#microphone)

## 최선이라고 판단하기 전에 필요한 증거

1. **한국어·짧은 구간:** 실제 소유자의 자연스러운 “GPT야”와 2~3초 명령, 긴 명령을 분리한다. 다른 날·거리·마이크·소음 조건을 포함한다. 등록·임계값 조정용 자료와 최종 평가 자료를 나눈다.
2. **화자와 겹침:** 타인만 말하기, 소유자 뒤에 타인이 이어 말하기, 동시에 말하기, TV/스피커 배경을 검사한다. 잘못 승인한 횟수와 전체 비소유자 시도 수, 소유자 거절·명령 오인식·겹침 누락을 각각 보고한다. 하나의 “정확도 %”로 합치지 않는다.
3. **재생·합성:** 등록자의 녹음을 휴대전화 스피커로 재생하는 경우를 별도 시험한다. 화자 임베딩 모델은 살아 있는 본인인지 확인하는 모델이 아니다. ASVspoof가 physical access/replay와 합성 음성의 countermeasure를 별도 평가하는 이유다. 현재 후보의 anti-spoof 성능을 검증하지 않았으며 생체 인증 보장을 하지 않는다. [ASVspoof 공식 범위](https://www.asvspoof.org/index2021.html)
4. **단말·개인정보:** Fold4에서 시작·반복 명령·통화/화면전환 후 복구를 측정하고, 네트워크를 감시해 허용된 텍스트 외 원음·특징 벡터가 나가지 않는지 확인한다. 모델 로딩 실패와 과부하 때도 전송을 막아야 한다.
5. **모델 교체:** 동일 평가셋·동일 허용 오수락 기준에서 거절률·호출어 성공·명령 오류·지연·메모리·전력·유지보수 비용을 비교한다. 다른 논문의 EER/DER 숫자로 승자를 정하지 않는다. q8 변환 전후도 같은 조건에서 비교한다.

**미검토 범위:** 위 후보의 한국어/Fold4 실기 비교, ReDimNet2 ONNX 변환, ERes2NetV2 Android 통합, anti-spoof 전용 모델, 전체 2026 음성 연구·상용제품, 최신 한국어 ASR 전체 순위는 아직 검증하지 않았다. 따라서 현재 결론은 “공개 자료로 선정한 로컬 구현 기준선과 다음 비교 후보”까지다.
