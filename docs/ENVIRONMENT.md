# Phase 0 환경 및 기존 시스템 조사

조사일: 2026-09-26 (Asia/Seoul). 현재 PC의 읽기 전용 조사와 공식 문서 기준이다. **모델 실행·설치·로그인 변경·빌드·테스트·서버 기동·배포는 수행하지 않았다.** 소스 조사와 실제 운영 검증을 구분한다.

## 1. 로컬 환경

| 항목 | 확인 결과 | 근거·한계 |
|---|---|---|
| Office 저장소 | `C:/Users/USER/OneDrive/daslab-ai-office` | 현재 작업 디렉터리 |
| Windows | NT 10.0.26200, DisplayVersion 25H2, UBR 9457 | 사용자가 지정한 Windows 11 환경. 레지스트리 ProductName은 `Windows 10 Pro`로 출력되므로 원시 표기와 사용자 설명을 구분 |
| 셸 | PowerShell Core 7.6.5 | `$PSVersionTable` |
| Git Bash | GNU bash 5.2.37(1), x86_64-pc-msys | `C:/Program Files/Git/bin/bash.exe --version`; 현재 PATH의 `bash` 명령으로는 발견 안 됨 |
| Python | 3.12.1 | `C:/Program Files/Python312/python.exe` |
| SQLite | 3.43.1 | Python 표준 라이브러리 |
| git | 2.53.0.windows.2 | `git --version` |
| Claude CLI | 현재 PATH와 확인한 표준 후보 경로에서 미발견 | 설치 여부를 PC 전체로 단정하지 않음 |
| Codex CLI | `0.155.0-alpha.16.4` | `codex --version` |
| Codex 실행 파일 | `C:/Users/USER/AppData/Local/OpenAI/Codex/bin/13995fba801849b0/codex.exe` | 버전 포함 경로이므로 이후 실행 시 재탐색 |
| 허용 추가 패키지 | pyyaml, openpyxl, pytest 모두 현재 Python에 미설치 | `importlib.metadata.version`; 이번에 설치하지 않음 |

Git 기본 조사에서 전역 ignore 파일 읽기 권한 경고가 나왔다. 저장소별 재현 비교에는 사용자 설정을 쓰지 않는 일회성 `core.excludesFile` override를 사용했으며 git 설정 파일은 바꾸지 않았다.

## 2. 인증·환경 점검: SPEC 6장 1–4번 수동 조사

| 점검 | 결과 | 판정 |
|---|---|---|
| `ANTHROPIC_API_KEY` 존재 | false | PASS, 값 조회·출력 안 함 |
| `ANTHROPIC_AUTH_TOKEN` 존재 | false | PASS, 값 조회·출력 안 함 |
| `OPENAI_API_KEY` 존재 | false | PASS, 값 조회·출력 안 함 |
| `CODEX_API_KEY` 존재 | false | PASS, 값 조회·출력 안 함 |
| `claude auth status` | command not found; 실제 인증 상태 확인 불가 | BLOCKED |
| `codex login status` | `Not logged in`, exit 1 | FAIL in this shell |
| `~/.codex/config.toml`의 `forced_login_method` | 루트 키 UNSET | WARN; `chatgpt` 강제값 아님 |

인증 파일·토큰은 읽지 않았다. 인증 상태는 위 status 명령의 판정만 사용했으며 계정 식별정보는 출력하지 않았다. Codex 데스크톱 앱 대화가 가능하다는 사실과 이 별도 CLI 프로세스의 인증 결과는 구분한다. 같은 대표 셸에서 재확인할 때도 status 명령만 사용한다.

허용된 `config.toml`에서 읽어 출력한 추가 항목: `model = gpt-6-astra`, `model_provider = UNSET`, `sandbox_mode = UNSET`, `windows.sandbox = elevated`. 다른 설정 내용은 출력·복사하지 않았다. 설정 수정 없음.

## 3. 실제 설치본에서 확인한 CLI 인터페이스

### Codex

`codex --help`, `codex exec --help`, `codex login --help`, `codex sandbox --help`를 실행했다. 도움말 확인은 모델 호출이 아니다.

| 옵션/명령 | 설치본 확인 내용 | 향후 적용 |
|---|---|---|
| `exec` | 비대화형 실행 | 승인된 저빈도 작업에 한함 |
| `--model`, `-m` | MODEL 문자열 | 설정값 전달; 유효 계정 접근은 스모크에서 확인 |
| `--json` | stdout JSONL 이벤트 | 단일 JSON 문서로 오인하지 않고 이벤트와 최종 결과 분리 |
| `--output-schema FILE` | 최종 응답 JSON Schema 파일 | 자체 결과 검증도 수행 |
| `--output-last-message FILE`, `-o` | 마지막 응답 파일 | 고객이면 clients 내부 경로만 |
| `--sandbox`, `-s` | `read-only`, `workspace-write`, `danger-full-access` | 마지막 값은 사용 금지; 권한별 최소 범위 |
| `--cd`, `-C` | 작업 루트 | 읽기·쓰기 경계 확인 |
| `--ephemeral` | 세션 파일 지속 저장 없이 실행 | 고객 데이터 경계 검증의 후보, 모든 로그 무기록 보증으로 해석하지 않음 |
| `--ignore-user-config` | 사용자 config 무시, auth는 CODEX_HOME 사용 | 정책·플러그인·로그 격리 설계 때 검토; 이번에 사용 안 함 |
| `--color never` | 색상 없는 출력 | 파서 입력 안정화 |
| PROMPT `-` | stdin에서 지시 입력 | 셸 명령에 고객 원문 보간 금지 |
| 최상위 `--ask-for-approval` | `on-request`, `never` | exec 도움말에 직접 나열되지는 않음; 코드에서 임의 결합 추측 금지 |

설치본에 없는 `--full-auto` 같은 과거 예제를 복사하지 않는다. `--dangerously-bypass-approvals-and-sandbox`, `--oss`, 다른 provider로 우회하는 옵션은 사내 구독 경로에서 제외한다. 구체적인 argv 조합은 Phase 1에서 설치본과 테스트로 확인한다.

### Claude

`claude --version` 시도는 command not found로 실패했다. `claude --help`, `claude -p --help`, `claude auth status`도 명령 탐색 실패로 실행할 수 없었다. 다음 3개 후보는 파일이 없었다.

- `C:/Users/USER/.local/bin/claude.exe`
- `C:/Users/USER/AppData/Roaming/npm/claude.cmd`
- `C:/Users/USER/AppData/Local/Programs/Claude/claude.exe`

공식 문서에는 `-p`, `--output-format json`, `--json-schema`, `--model`, `--tools`, `--permission-mode`가 기재되어 있다. `--allowedTools`는 허용된 도구만 남기는 기능과 다르므로 `--tools`와 혼동하지 않는다. **이 PC의 검증된 플래그 목록은 아직 없다.** 설치본 help 확인 전 실제 호출 명령을 확정하지 않는다. [Claude CLI reference](https://code.claude.com/docs/en/cli-reference)

## 4. 모델 값 확인 수준

| 백엔드/역할 | 값 | 증거 | 아직 확인하지 못한 것 |
|---|---|---|---|
| Codex 기본 후보 | `gpt-6-astra` | 현재 config, 비인증 파일 `models_cache.json`의 공개 표시 모델 목록, 공식 모델 문서 | 현재 CLI 로그인 계정에서 실제 호출 성공 |
| Codex 추가 후보 | `gpt-6-sol`, `gpt-6-luna` | 캐시와 공식 문서 | 역할 배정·구독 이용 가능 여부 |
| Codex 이전 계열 | `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5` | 캐시의 표시 목록 | 이번 기본 모델로 선택하지 않음 |
| Claude 비서실장 | `opus` | 공식 문서상의 모델 alias | 설치본·계정·실제 해석 모델 전부 미확인 |
| Claude researcher/marketer | `sonnet` | 공식 문서상의 모델 alias | 동일 |
| Claude ops | `haiku` | 공식 문서상의 모델 alias | 동일 |

캐시의 client_version은 `0.155.0`. 캐시는 자격증명이 아니며 계정 인증의 증거로 사용하지 않았다. alias 해석은 제공자·설정에 따라 달라지므로 최신 특정 Claude 모델명을 추정해 넣지 않는다. **SPEC P10의 양쪽 실제 모델 검증은 미완료**다. [Codex models](https://learn.chatgpt.com/docs/models), [Claude model configuration](https://code.claude.com/docs/en/model-config)

## 5. Windows 샌드박스와 재사용 지시

설치본의 `codex sandbox --help`는 Windows restricted token sandbox 실행 기능을 보여 준다. 로컬 설정은 `[windows] sandbox = "elevated"`다. 공식 문서의 elevated 모드는 별도 낮은 권한 사용자·파일 권한·방화벽 경계를 사용하며, unelevated는 현재 사용자 기반 제한 토큰 방식이다. **설정과 문서 확인까지 완료했으며, 별도 sandbox 설치·경계 침범 실험·고객 격리 보장은 이번에 검증하지 않았다.** `read-only` 선택만으로 고객 간 읽기 격리가 완성됐다고 간주하지 않는다. [Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)

| 대상 | 확인한 지원 방식 | 채택 시점 |
|---|---|---|
| Codex 프로젝트 지시 | 루트 AGENTS.md | Phase 1: 150줄 이하로 현재 SPEC·Phase 연결 |
| Codex 재사용 스킬 | `.agents/skills/<name>/SKILL.md`, 명시적 `$이름` 호출 | Phase 2 |
| Codex custom prompts | 공식 문서상 deprecated | 신규 기반으로 쓰지 않음 |
| Claude 프로젝트 지시 | CLAUDE.md; 사용자 요구에 따라 첫 줄 `@AGENTS.md` | Phase 1 |
| Claude 스킬 | `.claude/skills/<name>/SKILL.md`, `/이름` 호출 | Phase 2; 설치본 검증 필요 |
| Claude subagents | `.claude/agents/*.md`, YAML frontmatter + 역할 본문 | Phase 1 역할 설계/Phase 2 워크플로; 설치본 검증 필요 |

출처: [Codex skills](https://learn.chatgpt.com/docs/build-skills), [Custom prompts](https://learn.chatgpt.com/docs/custom-prompts), [Claude skills](https://code.claude.com/docs/en/skills), [Claude subagents](https://code.claude.com/docs/en/sub-agents). 스킬·역할 파일을 만든 것과 백그라운드 직원/일정이 가동되는 것은 별개다.

## 6. 홈페이지 — 읽기 전용 조사

경로: `C:/Users/USER/OneDrive/daslab/daslabhp`. HEAD `60f7f9a380fd5fd334ab43813dc1e0b2a13a56dd`.

| 항목 | 확인 결과·근거 |
|---|---|
| 기술 스택 | 정적 HTML/CSS/JS + pnpm workspace, React 19.2.6 / TS 5.9.3 / Vite 8.0.13 데모 9개, Three.js 0.185 계열·Recharts. Node ≥22.13.0, pnpm 11.25.0. `package.json:6`, `demos/manufacturing-pump-assembly/package.json:15` |
| 실행 방법 | `pnpm install --frozen-lockfile` → `pnpm check` → `pnpm preview`; 홈 개발은 `pnpm dev:home`, localhost 4173. `LOCAL-DEVELOPMENT.md:7`, `package.json:9`. 조사 중 실행 안 함 |
| 입출력 | 조건·프리셋·seed → 메모리 내 KPI/이벤트/2D·3D 화면. 공용 엔진 `params`, `advance(until)`, `snapshot()`, `assertInvariants()`; snapshot의 metrics/entities/resources/events. `packages/demo-kit/src/engine.ts:12` |
| 빌드 산출물 | 각 데모 dist를 루트 dist로 조립. 조립이 dist를 삭제·재생성하므로 읽기 전용 조사에서는 실행 금지. `scripts/assemble-site.mjs:67` |
| 공개 설정 | 6개 데모 배포 manifest; 나머지 3개는 제외. `scripts/demo-manifest.mjs:2` |
| URL | `https://daslab.co.kr`, `CNAME:1`, `index.html:15`에 근거. 원격 가동 확인 아님 |
| 외부 API | 문의 폼이 Formspree로 POST: `index.html:375`, `:429`. 고객 입력 전송은 L3; 제출하지 않음. Google Fonts 정적 자원, 공급망 GeoJSON은 같은 사이트 fetch |
| 모델 키/SDK | 안전한 소스 검색에서 주요 모델 API-key 변수나 OpenAI/Anthropic SDK 참조 미검출. .env·인증 파일은 열지 않아 실제 외부 환경은 판단하지 않음 |
| 배포 | main push/workflow_dispatch가 GitHub Pages 배포를 유발할 수 있음. `.github/workflows/pages.yml:3`, `:64`. push·dispatch 안 함 |

연동 후보: Office에서 manifest·README·설정을 읽어 목록/점검 초안을 만들 수 있다. 공개 페이지 GET 상태 확인은 향후 L0 작업이다. 시뮬레이션 입력 JSON→출력 JSON용 CLI/HTTP 서비스는 발견하지 못했다. 내보낸 TS 엔진용 Office 측 어댑터는 제안일 뿐이며 빌드·의존성·직렬화 검증이 필요하다.

## 7. 매장 플래너 — 읽기 전용 조사

경로: `C:/Users/USER/OneDrive/공부/whitesushi`. 도면·3D·DES·재무·제안서 기능의 소스 일치로 확인했다. HEAD `787f1beb9ff003d1b4b77f45ef2799a1af7c66df`.

| 항목 | 확인 결과·근거 |
|---|---|
| 기술 스택 | React 19.1, TS 5.9, Vite 6.4, PDF.js 4.10.38, Three.js 0.180, fflate; Vitest/Playwright. `package.json:6,13,21` |
| 실행 방법 | Node ≥22.12, `npm run dev`; 기본 `http://127.0.0.1:5173`, strict port. `README.md:13,19,34`, `vite.config.ts:6`. 정의만 확인, 실행 안 함 |
| 입력 | PDF, FloorPlan JSON. mm 좌표·walls/doors/windows/zones/objects·요소 ID·치수·회전·출처·검토 상태. `src/modules/space/types.ts:5,44`, `model.ts:16`, `pdfImport.ts:56,227` |
| 출력 | FloorPlan JSON, GLB/GLTF, PNG, 6개 뷰 ZIP, XLSX 제안서. `README.md:90`, `src/application/exportProposal.ts:166,197` |
| 계산 | DES `src/modules/simulation/engine.ts:30`, 반복실험 `src/modules/scenario/execution.ts:25`, 민감도 `sensitivity.ts:8`, 재무 `src/modules/financial/model.ts:34` |
| 외부 API/키 | MockMarketProvider 사용·실상권 API 미연동. `src/application/workflowAnalysis.ts:39`, `mockMarketProvider.ts:69`, `docs/data-sources.md:38`. 안전한 소스 검색에서 API 키 변수 미검출 |
| 환경 변수 | 확인한 변수명은 VITE_BASE_PATH, PLAYWRIGHT_CHROME_PATH, BASE_URL; API 인증 키 아님. .env·인증 파일을 읽지 않음 |
| 네트워크 | 같은 배포의 제안서 서식/원본 오버레이 fetch. `exportProposal.ts:171`, `SpaceWorkspace.tsx:297` |
| 저장·서비스 | 브라우저 메모리; 전체 프로젝트 저장/복원·DB·계정·분석 HTTP/CLI 없음. `README.md:52`, `docs/product-workflow.md:68`. `src/core/ports.ts:32`의 ProjectRepository는 인터페이스 |

`analyzeStoreProject` (`src/application/storeAnalysis.ts:25`), `compareStoreScenarios` (`scenarioAnalysis.ts:78`), `buildProposalWorkbook(input, templateBytes?) → Uint8Array` (`exportProposal.ts:166`) 등 TypeScript 함수 export는 있다. **함수 export가 Office에서 즉시 호출 가능한 CLI/HTTP 인터페이스라는 뜻은 아니다.** 우선 입력 FloorPlan JSON 및 내보낸 XLSX 파일 계약을 정리하고, 후속에 Office 측 별도 어댑터/3D 전기 레이어 제안서를 작성한다. 저장소 변경은 별도 승인 대상이다.

`scripts/start.ps1:26`은 의존성이 없으면 `npm ci`를 자동 실행하므로 조사에서 사용하지 않았다. 실제 UI·계산·사업 성과는 검증하지 않았다. 기존 미추적 PDF 1개는 열지 않았으며 그대로 보존했다.

## 8. 기존 AI Office에서 확인한 전환 차이

| 현 상태 | 근거 | 계획 |
|---|---|---|
| doctor/run/ledger용 CLI 진입점 없음 | `office/__main__.py` 없음, `python -m office doctor` exit 1 | Phase 1 신규 CLI |
| 서버 15초 큐 폴링·UI 4/15초 폴링 | `office/service.py:29,57,144`, `static/app.js:112` | 새 경로는 이벤트/명령 기반; 기존 경로 이관 별도 |
| 자식 환경 allowlist만 있고 부모 API 키 존재 시 중단 안 함 | `office/worker.py:49` | 공통 사전 차단 |
| 공용 data/runs에 입력·프롬프트 저장 | `office/service.py:175,204`, `office/worker.py:222` | 고객별 clients 격리 |
| 단일 Codex + 코드 Reviewer | `office/providers.py:8`, `config/office.json:2` | 다른 CLI의 검토 강제 |
| `.gitignore`에 clients/, .env* 없음 | `.gitignore:1–12` | Phase 1 보완; 현재 clients 추적 파일은 0 |
| 기존 data/ 전체 ignore | `.gitignore:4` | 런타임 제외를 유지하며 unit-prices 시드 추적 |
| 음성은 기존 /api/missions로 연결 | `static/voice/app.mjs:126` | 기존 보안 원칙·기능 보존, 신규 core 연결은 별도 검증 |

이는 소스 조사이며 실제 앱 실행·독립 교차 모델 검토·기존 고객 데이터 내용 점검 결과가 아니다.
