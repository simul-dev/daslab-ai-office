# DAS Lab AI Office — 최소 설계와 구현 순서

작성: 2026-09-24 / 출처: 대표의 구축 요청, 로컬 환경 점검 / 검증: 구현 전 설계

구현 결과: 본 설계의 PoC 업무 흐름을 실제 Codex로 1회 완주했다. 최종 확인 범위는 [VERIFICATION.md](VERIFICATION.md)에 기록한다.

## 확인한 환경
- 신규 빈 프로젝트. Windows, Python 3.12.1, Node 22.15.0.
- VS Code 확장에 포함된 Codex CLI 0.155.0-alpha.16.3 발견.
- `codex login status`: ChatGPT 로그인 확인. 인증 파일은 읽거나 복사하지 않는다.
- 공식 비대화형 실행 경로 `codex exec` 사용. ChatGPT 인증만 허용하며 API 키 경로로 전환하지 않는다.
- 공식 근거: https://developers.openai.com/codex/noninteractive/ 및 https://developers.openai.com/codex/auth/ (2026-09-24 확인).

## 구조 및 범위
- Python 표준 라이브러리 HTTP 서버 + SQLite + HTML/CSS/JavaScript. 외부 런타임 패키지 없이 시작.
- localhost 전용 단일 사용자 웹. 타 기기 공유, 외부 발송, 구매, 운영 배포 제외.
- 대표: 목표·입력·완료 기준·우선순위 등록, 수동 실행, 결과 검토·승인·반려.
- PM: 입력을 보존해 명세 작성, Codex 작업자 배정, 완료 기준 체크리스트 작성. 별도 AI 호출 없음.
- PM/Planner, Reviewer, Worker는 각각 Protocol 인터페이스와 provider 레지스트리로 분리한다. v0.1의 CodePlanner/CodeReviewer/CodexWorker를 추후 AI 구현체로 교체할 수 있다. 오케스트레이터에 계획·검증 로직을 결합하지 않는다.
- 실행 담당: 교체 가능한 Worker 인터페이스의 Codex 구현. 첫 버전은 분석·문서·코드 제안 산출물 작성. 임의 저장소 수정/프로그램 실행은 후속 단계.
- 검증 담당: 실제 종료 코드, CLI 완료 이벤트, 구조화 산출물, 파일·해시·출처·기준 대응 검사. 내용의 진실성·사업성은 대표 검토가 필요하며 자동 완료하지 않는다.
- 상태: queued(대기) → running(실행 중) → review(검토 필요) → completed(완료). 오류는 failed(실패), 취소는 cancelled(취소). 반려는 failed. 명시적 재실행만 허용.
- Codex 도구·MCP·외부 액션을 제한하고 읽기 전용으로 실행. 호스트가 최종 응답을 산출물로 저장한다.
- 동시 실행 1, 업무당 최대 3회, 한국 시간 기준 하루 10회, 실행당 600초. 자동 재실행 없음. 실제 비용·잔여 할당량은 확인 불가로 표시.
- 업무·실행·이벤트·검증·결정은 SQLite에 저장. 직원 지침과 프로젝트 지식은 별도 파일 저장. 실행별 입력 스냅샷·프롬프트·이벤트·결과·검증 파일 보존.
- 재시작 시 중단된 실행을 실패로 복구하며 자동 재개하지 않는다. 별도 실행 폴더는 보존한다.
- 모든 기록에 source, created_at, project_id, verification_status를 둔다. 온톨로지 자동 구축은 후속 단계.

## 구현 순서
1. 설계/API 계약, 직원 역할, 프로젝트 지식, 시험 업무 작성.
2. SQLite 저장소, 상태 전이, 실행 제한, PM 명세 구현.
3. Codex 어댑터, 분리된 실행 폴더, 타임아웃·취소·검증 구현.
4. 관리자 웹: 업무 목록·등록·상세·실행·결과·로그·검토.
5. 상태 전이·실패·제한·재시작 테스트와 브라우저 검증.
6. 대표의 시험 업무를 등록하고 실제 Codex 실행 → 파일 검사 → 검토 필요 확인. 증거를 docs/VERIFICATION.md에 기록.

## API 계약
- GET /api/health → {name, worker: {provider, available, auth_mode, version, message}, limits: {concurrency, max_attempts, daily_runs, timeout_seconds}, usage: {runs_today, active_runs}, projects: [{id,name}], roles: [...]}.
- GET /api/tasks → {tasks: [...]}.
- POST /api/tasks {title, project_id, goal, inputs, acceptance_criteria: string[], priority: high|normal|low} → task detail.
- GET /api/tasks/:id → {task, runs, events, decisions}. task: {id,title,project_id,goal,inputs,acceptance_criteria,priority,status,created_at,updated_at,error,pm_spec,source,verification_status}. runs: {id,attempt,status,started_at,finished_at,error,provider,artifacts:[{name,url,sha256,size}],verification,run_dir}.
- POST /api/tasks/:id/run {} → {run_id,status}; queued/failed/cancelled만 실행 가능.
- POST /api/tasks/:id/cancel {} → task detail.
- POST /api/tasks/:id/review {decision: approve|reject,note} → task detail. approve는 검증 통과한 review 상태에서만 가능.
- GET /api/runs/:id/files/:name → 허용된 산출물/기록 텍스트.
- GET /api/sample → 최초 시험 업무 입력값. 명시적 등록 후 실행하며 데모 결과 없음.
- 오류 응답 {error: 한국어 설명}. 상태 문자열 queued/running/review/completed/failed/cancelled.
- POST 요청은 Content-Type: application/json, X-DAS-Office: 1 필요. 동일 출처만 허용.

## 첫 시험 산출물 계약
구조화 JSON과 읽을 수 있는 Markdown 보고서. 확인된 문제(사용자 배경 출처)와 가정, 단일 MVP 및 이유, 입력/출력, 실제 업무 검증 계획 및 완료 기준, 후속 작업, 기준별 근거를 포함한다. 실제 고객 검증은 미실시로 표시한다. 근거 없는 절감률·시장규모·인터뷰 결과 금지.
