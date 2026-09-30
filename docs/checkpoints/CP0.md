# CP0 — 조사·계획 완료, 실행 준비 조건 미해소

일자: 2026-09-26, Asia/Seoul. 책임: 리드 엔지니어. 조사 분담: 홈페이지·매장 플래너·기존 Office 차이 검토의 임시 Codex subagent 3개. 상주 직원이나 다른 백엔드 검증자는 아니다.

기록된 작업 구간: 2026-09-26 03:39:17–03:54:59 (Asia/Seoul), 15.7분. 원문 저장 시점부터 최종 문서 검토까지의 경과 시간이다.

## 1. 요약 3줄

1. 원문 SPEC 보존, 단계별 계획·결정·환경 조사와 두 기존 시스템의 읽기 전용 조사를 완료했다.
2. 이번 변경은 Markdown 문서 6개뿐이며 코드·설정·기존 시스템 변경은 0건이다. 기존 PLAN과 진행 중 변경을 보존했다.
3. Claude 명령 미발견, Codex CLI 로그인 실패, 실제 모델 검증 미완료를 선행 조건으로 남기고 Phase 0에서 정지한다.

## 2. 변경 파일 목록

| 파일 | 변경·역할 |
|---|---|
| [SPEC.md](../SPEC.md) | 추가. 사용자 첨부 원문을 바이트 그대로 복사 |
| [PLAN.md](../PLAN.md) | 수정. 조직 v0.4 및 Phase 0–5 계획·수용 기준 |
| [DECISIONS.md](../DECISIONS.md) | 추가. 구조 확정·이관·권한·고객 격리·모델 결정 |
| [ENVIRONMENT.md](../ENVIRONMENT.md) | 추가. 환경·CLI·인증·모델·스킬·두 시스템 조사 |
| [이전 PLAN 보존본](../PLAN-pre-v04-2026-09-26.md) | 추가. 기존 미커밋 PLAN의 원본 보존 |
| [CP0.md](CP0.md) | 추가. 이번 결과·제약·승인 정지점 |

루트 AGENTS.md, 코드, 설정, .gitignore, package/lock, knowledge, 기존 음성 자산과 런타임 DB는 수정하지 않았다. 기존 git 변경 목록이 깨끗하지 않은 것은 이전 작업 때문이다. commit/push도 하지 않았다.

## 3. 실행 방법

지금 검토할 문서와 읽기 전용 재확인 명령이다. 신규 `office` 기능을 실행하는 명령으로 오인하지 않는다.

```powershell
Set-Location -LiteralPath 'C:\Users\USER\OneDrive\daslab-ai-office'
Get-Content -LiteralPath 'docs/checkpoints/CP0.md' -Raw
Get-Content -LiteralPath 'docs/ENVIRONMENT.md' -Raw
git diff --check -- docs/PLAN.md
codex --version
codex --help
codex exec --help
```

인증 재확인은 `claude auth status`, `codex login status`만 사용한다. Claude가 현재 셸에서 없으므로 먼저 실행 파일 경로를 확인해야 한다. 출력의 이메일·계정 ID를 보고서에 그대로 붙이지 않는다. `auth.json`, 자격증명 파일, 환경변수 값은 읽거나 출력하지 않는다.

## 4. 검증 결과

| 확인 | 결과 | 증거·한계 |
|---|---|---|
| SPEC 원문 보존 | PASS | 첨부/복사본 SHA256 모두 `D015AEADE05D50EB4B9FD0EBD072C17657E153EC488C740A501E82FC30D15647` |
| 기존 PLAN 보존 | PASS | 수정 전/보존본 SHA256 모두 `EF7359052EFEC71454B1465DB0975ABC4504231BF8ED1EF1203C7F44BEEAB488` |
| Office 문서 외 변경 | PASS, 0건 | 기준선의 비Markdown 44개 SHA256 동일, 삭제 0. SPEC 복사 직후 전체 기준선 55개; 이후 추가/변경도 Markdown만 |
| 홈페이지 변경 | PASS, 0건 | 시작/종료 git status 빈 목록; 선택한 소스 179개 해시 집계 동일 |
| 매장 플래너 변경 | PASS, 0건 | 시작/종료 HEAD·status 동일. 기존 미추적 PDF 1개 보존. 선택한 추적 소스 127개 해시 집계 동일 |
| 문서 whitespace | PASS | `git diff --check -- docs/PLAN.md`, exit 0. LF→CRLF 안내는 오류 아님 |
| 문서 구성/링크 | PASS | 6개 문서의 Markdown 상대 링크·코드 블록, CP0 1–9 항목과 마지막 STOP 문구 확인 |
| Python/CLI/스킬 지원 조사 | 완료, 제한 명시 | 설치본 help 및 공식 문서. Claude 설치본·모델·실제 샌드박스 경계 실험은 미완료 |
| 구현·fake·마스킹 테스트 | 미실행 | Phase 0 코드 작성 금지, 신규 기능 미구현. pytest 미설치; 테스트 성공을 주장하지 않음 |
| 실제 모델 스모크 | 미실행 | Phase 1 범위. 인증 선행 조건 미해소 |

홈페이지 집계 SHA256: `89b8f1f10cf65c3ebe01af378dbb4a5b7d8239a0e06e2b50d76bb4f13f20fd7a`.

매장 플래너 집계 SHA256: `7FE56ADFBDA2909A77F442131DB6F649606D6F2EFB896E81502A4F9A23333CB2`.

해시 범위는 안전한 소스·설정·문서다. 인증·환경파일·고객 원자료·node_modules·dist·ignored 런타임 및 바이너리를 읽지 않았다. 따라서 디스크 전체나 다른 동시 프로세스의 무변경 보장은 아니다. 에이전트가 수행한 쓰기는 위 문서 6개에 한정했다. 플래너 경로 탐색 중 제외한 tomorrowhouse 후보도 시작/종료 clean 상태였다.

## 5. 구독 실행 증빙

### doctor 전체 출력

신규 doctor는 구현 전이다. `.pyc` 생성을 막는 프로세스 환경에서 실제 `python -m office doctor`를 실행했으며 다음과 같이 실패했다.

```text
C:\Program Files\Python312\python.exe: No module named office.__main__; 'office' is a package and cannot be directly executed
exit_code=1
```

이 출력은 doctor 통과가 아니다. SPEC 6장 1–4번은 수동으로 조사했다.

### 인증 방식과 금지 환경변수

```text
ANTHROPIC_API_KEY present=false
ANTHROPIC_AUTH_TOKEN present=false
OPENAI_API_KEY present=false
CODEX_API_KEY present=false
claude auth status: BLOCKED — claude command not found
codex login status: Not logged in; exit_code=1
forced_login_method: UNSET — WARN
```

현재 셸에서의 결과이며 데스크톱 앱 로그인 상태와 같다고 단정하지 않는다. status 출력을 내부에서 분류해 인증 방식/실패만 기록했고, 이메일·계정 ID·자격증명 값은 출력하지 않았다. 인증 파일을 직접 읽지 않았다.

### SDK/API_KEY 저장소 스캔

실행: `git grep -nE "(import|from) (anthropic|openai)|API_KEY"`에 전역 ignore 경고 회피용 일회성 옵션을 적용. exit 0, 5개 행. 원문 값 대신 위치와 매칭 이름만 기록했다.

| 위치 | 매칭 | 설명 |
|---|---|---|
| `tests/test_worker.py:39` | OPENAI_API_KEY | 자식 환경에서 제외되는지 확인하는 기존 테스트 |
| `tests/test_worker.py:40` | CODEX_API_KEY | 동일 |
| `tests/test_worker.py:48` | OPENAI_API_KEY | 테스트용 가짜 환경값 |
| `tests/test_worker.py:49` | CODEX_API_KEY, ANTHROPIC_API_KEY | 테스트용 가짜 환경값 |
| `tests/test_worker.py:79` | OPENAI_API_KEY | 자식 환경 제외 검증 |

git grep은 추적 파일 검색이므로 이번에 추가한 미추적 SPEC/ENVIRONMENT 등의 설명용 변수명은 이 5개 행에 포함되지 않는다. `office`, `tests`, `server.py`, `config`의 안전한 Python/JSON에 대한 추가 rg 검색도 같은 key 참조만 반환했다. `ANTHROPIC_AUTH_TOKEN`, OpenAI/Anthropic SDK import 매칭은 없었다. 기존 `clean_env`는 allowlist 방식이므로 문자열 미검출이 부모 키 차단 구현을 뜻하지 않는다. 신규 문서의 변수명은 설명용이며 실행 코드가 아니다. Phase 1 doctor는 추적·미추적 실행 코드 검사와 문서/테스트 예외를 구분해 구현해야 한다. 전체 문서 검색의 매치는 설명용으로 별도 보고한다(D11).

### 이번 단계의 모델 호출·원장

| 백엔드 | 모델 | 목적 | 시각/건수 |
|---|---|---|---|
| claude_cli | 미확정 | 실제 스모크/업무 호출 없음 | 0회 |
| codex_cli | 실행 없음, 설정 후보 gpt-6-astra | 실제 스모크/업무 호출 없음 | 0회 |

신규 실행 원장 자체가 아직 없어 **원장 조회 증빙은 N/A**다. 0회는 이번 작업에서 별도 CLI 모델 실행을 시작하지 않았다는 뜻이다. 현재 Codex 대화와 임시 조사 subagent 3개의 플랫폼 실행은 Office의 구독 CLI 스모크나 SQLite 원장 기록으로 계산하지 않았다. 동일 Codex 조사 분담을 Claude↔Codex 교차 검증으로 보고하지 않는다.

## 6. 데이터 안전

- 고객 원문·도면 PDF·인증 파일·.env·기존 data/runs 내용을 읽거나 옮기지 않았다. 원문 SPEC의 제공된 시드 외에 새로운 고객 자료를 공용 문서에 복사하지 않았다.
- 목표 저장 위치는 `clients/<id>/`; 원본 전사 지정 경로는 현재 존재하지 않는다. 임의 전사나 원자료를 만들어 수용 검증을 통과시키지 않았다.
- 현재 `.gitignore`에는 `clients/`, `.env*`가 없고 `git check-ignore --no-index clients/phase0-probe.txt .env .env.local`은 매칭 없음. `git ls-files -- clients/**` 결과는 0건이다. Phase 0에서 .gitignore는 수정하지 않았다.
- 현재 고객 격리·마스킹 구현 검증은 **미완료**다. 로그·원장·보고서·커밋 마스킹과 고객별 경계 테스트는 Phase 1 수용 기준이다.
- 구독 CLI도 외부 모델로 입력을 전송한다. 고객 폴더 내 저장이 전송 승인을 대신하지 않는다. 실제 고객 PDF/전사의 CLI 처리는 D21의 L3 경계를 확정한 뒤 다루며 이번 전송은 0건이다.

## 7. 대표 결정 사항

| 선택지 | 결과 | 추천 |
|---|---|---|
| CP0를 검토하고 보완/대기 | 문서만 유지, 다음 단계 시작 안 함 | 현재 기본 상태 |
| `승인: Phase 1 진행` | 뼈대·정책·fake 테스트 구현 시작. CLI 설치/인증·모델 문제 해결 후에만 실제 스모크 | 다음 단계 진행 시 추천 |

경로 질문은 로컬 탐색으로 해소했다. Claude CLI의 설치 위치/구독 로그인과 Codex CLI 상태 재확인은 실제 호출 전 필요한 환경 조치다. 이번에 설치·로그인·계정 설정을 변경하지 않았고, 이를 해결하지 않은 채 CP1 성공을 선언하지 않는다.

## 8. 다음 단계 계획

승인 후 신규 CLI·조직·권한·고객 경계를 먼저 만들고 fake 테스트와 doctor를 완성한다. 공식 CLI 두 개의 help·status·모델을 재확인한 다음, 고객 데이터 없는 OK 스모크를 각 1회 수행한다. 자세한 작업·수용 기준은 [PLAN](../PLAN.md)에 있다. 지금은 Phase 1을 시작하지 않았다.

동일 Codex의 별도 subagent가 PLAN/DECISIONS/ENVIRONMENT의 정합성을 검토했다. 고객 CLI 전송 경계와 키 문자열 검색 범위의 모호성 2건을 D21/D11에 반영했다. 이는 문서 검토이며 다른 CLI 백엔드에 의한 실행 검증은 아니다.

## 9. 정지

정지 근거는 사용자 제공 [SPEC 작업 규칙 1](../SPEC.md)의 “각 단계가 끝나면 docs/checkpoints/CP{n}.md를 쓰고 멈춘다”와 마지막 “Phase 0만 수행” 지시다. 추가적인 도구/스킬 승인 절차를 만든 것이 아니다.

STOP: 대표 승인 대기
