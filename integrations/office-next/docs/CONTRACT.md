# Paperclip isolated trial contract

Reviewed 2026-10-01. This is a source/API contract, not a completed live agent trial.

## Versions and authority

- Trial runtime: `paperclipai@2026.916.1`, installed outside this repository at `%LOCALAPPDATA%/DASLab/ai-office-next/runtime`.
- Runtime packages actually inspected: `@paperclipai/server`, `@paperclipai/shared`, `@paperclipai/adapter-codex-local`, and `@paperclipai/adapter-utils`, all `2026.916.1`.
- Comparison checkout: `paperclipai/paperclip` commit `467125fafb47a8520856504fecc48d6e32055db1`, `%LOCALAPPDATA%/DASLab/ai-office-next/vendor/paperclip`.
- Online documentation is rolling documentation. The installed package's validators and route implementations govern the trial. Do not silently substitute main-branch behavior.

Observed differences:

| Item | Rolling docs / main | Installed 2026.916.1 |
| --- | --- | --- |
| Codex `engine:auto` | Docs describe ACP preference with CLI fallback; main config UI warns ACP may fail instead | Use explicit `engine: "cli"`; avoid depending on fallback |
| Sandbox bypass default | Main `codex-args.ts` can default to full bypass | Installed `codex-args.js` defaults to false; still explicitly set false |
| Agent prompt configuration | Codex docs list `promptTemplate` | New-agent API rejects `promptTemplate` and `bootstrapPromptTemplate`; use `instructionsBundle` |
| Issue create title | Main can derive a title from description | Installed validator requires a nonempty `title`; always send it |

## Isolation and authority

1. Serve the trial only on a new loopback port. `local_trusted` has no login and treats unauthenticated local requests as the board. It must not be published through Cloudflare or bound to LAN.
2. Keep runtime and database in the isolated trial directory and work files in the configured isolated workspace. On Windows this workspace is `%USERPROFILE%\\AI-Office-Next\\workspace`, outside app-package storage. Never point a trial agent's `cwd` at the existing office worktree, OneDrive checkout, or operating data directory. Repeated bootstrap uses the saved workspace and preserves local action/artifact settings.
3. Preserve the current office server, port 8772/8774, tunnel, pairing login, and `das-lab` recurring operation. No bridge submits the same work to both systems. Trial tasks have their own company ID and idempotency keys.
4. Agents use their run-scoped bearer token and matching `X-Paperclip-Run-Id` for writes. Do not test review enforcement with board-authenticated requests: the board has override privileges.
5. This local trial is not a hostile-worker security sandbox: a process able to call unauthenticated local board endpoints has a broader trust boundary than an isolated remote principal. Do not claim role prompts alone enforce filesystem or host-network isolation.
6. Do not enable routines, timer heartbeats, automatic company creation by agents, or external publication as part of the first execution trial.

## Subscription authentication and Codex home

Recommended adapter fields:

```json
{
  "engine": "cli",
  "command": "<absolute existing Codex executable path>",
  "cwd": "<absolute isolated trial workspace>",
  "dangerouslyBypassApprovalsAndSandbox": false,
  "fastMode": false,
  "timeoutSec": 900,
  "graceSec": 20
}
```

Keep the owner's existing model configuration unless the operator selects a model. Do not add provider API credentials or switch billing mode.

- Leave agent `env.CODEX_HOME` unset. The official adapter prepares a separate company home. An explicit override to the user's original home still receives Paperclip skill injection and managed MCP configuration, so it does not isolate that home.
- Installed `codex-home.js` copies static `config.json`, `config.toml`, and `instructions.md` on first seed; it **symlinks** `auth.json` to the existing login instead of copying rotating OAuth tokens. Codex runtime refreshes can still affect the shared login through that link; this is not a promise of zero authentication-file writes.
- Set the trial server's `PAPERCLIP_CODEX_AUTH_CACHE=0` if avoiding the optional cache-to-host credential refresh path. This does not disable Codex's own normal login refresh.
- Ensure inherited provider API-key variables cannot override subscription login. Check presence/auth mode without printing values. No API key should be added to adapter JSON.
- Windows must support the adapter's file symlink operation. Its helper rethrows unexpected symlink errors; do not silently replace failed links with copied tokens or the original global home. Record the concrete failure and resolve the runtime prerequisite separately.
- The adapter places `PAPERCLIP_API_KEY` in the child environment. `codex exec --json` receives its prompt over stdin. Invocation metadata passes environment values through key-name redaction (`key|token|secret|password|passwd|authorization|cookie`). Never print child environments or raw auth files, and do not put credentials in prompts, CLI arguments, artifacts, or API response reports.
- Source inspection establishes this intended data path. A successful environment probe and real run must still confirm local authentication works.

## API endpoints needed by the trial

All paths below are beneath the new instance's `/api` prefix.

| Operation | Method and path | Relevant contract |
| --- | --- | --- |
| Create isolated company | `POST /companies` | `{name, description?, budgetMonthlyCents?}` |
| Inspect / configure company | `GET`, `PATCH /companies/:companyId` | New-agent approval is changed only on the trial company |
| Create staff | `POST /companies/:companyId/agents` | Uses `createAgentSchema`; direct create is rejected if company requires hire approval |
| Inspect staff / chain | `GET /companies/:companyId/agents`, `GET /agents/:agentId` | Same-company reporting relationships, cycle validation |
| Change permissions | `PATCH /agents/:agentId/permissions` | Send both `canCreateAgents` and `canAssignTasks`; optional `canCreateSkills` |
| Probe adapter | `POST /companies/:companyId/adapters/codex_local/test-environment` | `{adapterConfig, agentId?}`; may seed managed auth home; not a pure read |
| Create task | `POST /companies/:companyId/issues` | Explicit `title`, `status`, `assigneeAgentId`, `parentId`, `idempotencyKey`, `executionPolicy` |
| Inspect task | `GET /issues/:issueId` | Observe status, assignee, execution state, run attribution |
| Claim task | `POST /issues/:issueId/checkout` | `{agentId, expectedStatuses:[...]}`; agent can claim only as itself; requires a run identity |
| Change status / submit review | `PATCH /issues/:issueId` | `{status, comment}`; use the current agent's bearer/run headers |
| Comment | `POST /issues/:issueId/comments` | `{body, clientRequestId?}`; UUID client ID supports duplicate protection |
| Read comments | `GET /issues/:issueId/comments` | Evidence and role handoff history |
| Explicit wake | `POST /agents/:agentId/wakeup` | `{source:"on_demand",triggerDetail:"manual",reason,payload:{issueId},idempotencyKey}` |
| Run state | `GET /companies/:companyId/heartbeat-runs`, `GET /heartbeat-runs/:runId` | Capture state and timestamps, not secret-bearing raw trace |
| Run activity | `GET /heartbeat-runs/:runId/events`, `GET /heartbeat-runs/:runId/log` | Inspect narrowly and redact before reporting |
| Stop a trial run | `POST /heartbeat-runs/:runId/cancel` | Only the identified trial run |
| Pause trial agent | `POST /agents/:agentId/pause` | Use only when intentionally disabling that employee's on-demand work |

`POST /agents/:id/heartbeat/invoke` also exists, but is a legacy route. Prefer `/wakeup` and a stable idempotency key. Task assignment/reassignment may already enqueue a wake; inspect existing runs before explicitly waking again.

## Staff payload

Use a short fixed roster. Staff creation is performed by the operator; trial workers cannot hire arbitrary new agents.

```json
{
  "name": "Trial PM",
  "role": "pm",
  "reportsTo": null,
  "adapterType": "codex_local",
  "adapterConfig": {
    "engine": "cli",
    "command": "<absolute Codex executable>",
    "cwd": "<isolated trial workspace>",
    "dangerouslyBypassApprovalsAndSandbox": false,
    "fastMode": false,
    "timeoutSec": 900
  },
  "instructionsBundle": {
    "entryFile": "AGENTS.md",
    "files": {"AGENTS.md": "<bounded role instructions; no secrets>"}
  },
  "runtimeConfig": {
    "heartbeat": {
      "enabled": false,
      "intervalSec": 0,
      "wakeOnDemand": true,
      "maxConcurrentRuns": 1
    }
  },
  "permissions": {"canCreateAgents": false, "canCreateSkills": false}
}
```

Set builder and QA `reportsTo` to the PM's returned UUID. Use the board permissions endpoint to grant the PM task assignment:

```json
{"canCreateAgents": false, "canCreateSkills": false, "canAssignTasks": true}
```

Set `canAssignTasks:false` for builder and QA unless the specific trial requires it. Do not make the PM `ceo` merely to obtain broad permissions. `heartbeat.enabled:false` disables timer work; it does **not** disable assignment/on-demand wakes. This is deliberate for the one-cycle trial.

## PM → builder → QA → PM loop

The owner task is assigned to PM. PM creates a bounded child issue with its parent ID, concrete artifact path, acceptance criteria, and the policy below. A missing input should be reported in that task, not replaced by an invented fact.

```json
{
  "title": "Trial: implement the bounded artifact",
  "description": "<input, artifact, acceptance criteria and permitted workspace>",
  "status": "todo",
  "assigneeAgentId": "<builder UUID>",
  "parentId": "<PM task UUID>",
  "idempotencyKey": "office-next-trial-001-build",
  "executionPolicy": {
    "mode": "normal",
    "commentRequired": true,
    "maxReviewRounds": 3,
    "stages": [
      {"type":"review","participants":[{"type":"agent","agentId":"<QA UUID>"}]},
      {"type":"approval","participants":[{"type":"agent","agentId":"<PM UUID>"}]}
    ]
  }
}
```

- Builder checks out its assigned task and writes the actual artifact. Its `PATCH {status:"done", comment:"<evidence>"}` is intercepted: task becomes `in_review`, assigned to QA, with execution stage pending.
- QA checks the artifact against the acceptance criteria. `PATCH {status:"in_progress", comment:"<specific failure and required correction>"}` records changes requested and returns the task to the original builder. The builder's next completion returns to that same QA stage.
- QA acceptance uses `PATCH {status:"done", comment:"<checks and evidence>"}`. The issue remains `in_review` and moves to PM approval.
- PM acceptance with a comment completes the policy and issue. PM then summarizes child evidence on the original goal.
- Require at least one real failing check and subsequent correction in the trial. An artificial role-play transcript, board-authenticated status edits, or a fixture that merely asserts success does not demonstrate the loop.
- For a non-dependency blocker, an agent can use `status:"blocked"` plus `unblockDescriptor:{owner:{agentId:"<its own agent UUID>"},action:"<specific next action>"}` and a comment. This descriptor tracks the next action; it does not transfer the work to a manager. The installed validator requires a structured owner and nonempty action of at most 2,000 characters. The PATCH route rejects an agent naming another agent, a user, or the board as unblock owner with 403 (`routes/issues.js:9242–9263`). A PM escalation therefore belongs in the authorized task/comment coordination flow; verify the PM's actual follow-up rather than claiming this descriptor wakes or assigns the PM. For child dependencies use the existing `blockedByIssueIds` relationship. Entering blocked needs an unresolved blocker, pending interaction/approval, or a valid descriptor (`routes/issues.js:9312`); a status-only blocked update is insufficient.
- `maxReviewRounds` only escalates to a human if the issue has a valid responsible/creating user. Verify the created issue's human attribution. Without one, the installed service keeps returning work to the executor even after the numeric cap; do not claim the cap alone prevents all loops.
- Policy stages support one required approval, even if several eligible participants are listed. Do not describe it as unanimous multi-reviewer approval.

## Proof to retain

Retain company/agent/task/run IDs (not tokens), installed versions, sanitized adapter mode, ordered task transitions, role-authored comments, artifact hashes, actual test output, one rejection/correction, PM disposition, and elapsed time. End-of-run claims should distinguish API configuration, real Codex execution, QA behavior, runtime gate behavior, browser visibility, and business impact.

Before a repeated test, inspect existing task by stored ID/idempotency key and active runs. Reuse the existing mission; do not add a second independent scheduler. After completion, preserve artifacts and verify that no active or queued trial run remains. Keep recurring heartbeat timers disabled. Employees may remain idle with on-demand wake enabled so the owner can submit the next explicit task; pause is for intentionally disabling that employee, not normal completion.

## Windows database note

The installed dependency set contains `embedded-postgres@18.1.0-beta.16` and its `windows-x64` native package. That confirms a Windows binary package is present, not that initialization/listening has passed. The trial must use its own PG data directory and port. Never stop an arbitrary `postgres.exe`, remove lock files, or reuse a production database to make initialization pass. Resolve errors against the exact trial process and directory only.

Read-only startup observation at 16:16 KST: the operator's first launcher exited after creating the isolated `paperclip` database and entering migrations. Its stderr reports `Failed query: CREATE EXTENSION IF NOT EXISTS pg_trgm;`. The native `pg_trgm.control` and `pg_trgm.dll` files both exist. The captured logs omit the underlying PostgreSQL error, so missing extension files, library-loading failure, and permissions must not be guessed. No trial listener was present on 3101/54329 at that check. This is a trial startup failure, not an application or agent-loop pass; later recovery evidence should supersede this observation.

## Windows process-adapter compatibility branch

The installed `codex_local` adapter's managed-home auth symlink failed with Windows `EPERM`. Copying rotating subscription credentials or using hard links is not the remedy. Pointing the adapter at the user's original Codex home would also permit skill injection and `config.toml` writes: `writeManagedCodexMcpConfig` writes even when there are no MCP gateways.

The compatibility branch uses Paperclip's installed `process` adapter to start the tracked `codex-worker.mjs`, which invokes the already installed native Codex CLI. This avoids the managed-home seed entirely. Native Codex retains ownership of its existing sign-in. Runtime policy is passed through one-run CLI options; this branch must not edit global Codex settings, copy credentials, switch to API billing, or relax an actual policy rejection.

Actual process-adapter contract in stable `2026.916.1`:

- `supportsLocalAgentJwt: true`; the harness supplies the run token in `PAPERCLIP_API_KEY`, plus run, agent, company and API identifiers. User-configured API keys cannot replace the harness token.
- The adapter does not export task ID, comment ID or wake context. The worker reads its own `GET /api/heartbeat-runs/{runId}` and uses `contextSnapshot.issueId`, the comment, `executionStage`, and `paperclipHarnessCheckedOut`. Same-company standard agents can read this endpoint; restricted trust modes can deny it and must fail closed.
- `inbox-lite` excludes `in_review`, so it cannot be the only source for QA or PM approval work.
- Review/approval participants must **not** call checkout on an `in_review` issue. The installed checkout service unconditionally writes `status: in_progress`. They validate `currentParticipant` and send the verdict PATCH directly. Executor work already checked out by the harness must not be checked out again.
- This adapter records process exit and stdout/stderr, not Codex-specific usage/session fields. A clean process exit does not prove the assigned objective or review policy completed.
- The worker must filter inherited environment variables, preserve only the run-scoped Paperclip credential needed by the API helper, and redact that value across stdout/stderr chunk boundaries. Do not put the token into CLI arguments, prompts, files, or reports. [Official Codex shell environment semantics](https://learn.chatgpt.com/docs/config-file/config-advanced#shell-environment-policy) describe inheritance and secret-name filters; verify actual child-shell availability without printing the value.

### Windows process ownership

Stable Paperclip's Windows process cancellation and adapter timeout terminate their direct child. A Node compatibility wrapper introduces another process level; relying only on the wrapper's exit can leave the native CLI or tool subprocesses alive.

`worker-watchdog.mjs` therefore exposes `attachWorkerWatchdog({child, executable, launchedAt, deadlineAt, env})`. Attach it after spawn and **before writing the work prompt**. Use a deadline earlier than the adapter's outer timeout. Failure to attach must stop this newly spawned worker instead of beginning work without supervision.

The watcher is a separate detached Node process. It checks parent/child identity, the actual run status and its bounded deadline. Only the freshly spawned child with matching PID, process start time and executable is owned. On cancellation, parent loss or deadline, PowerShell 7 rechecks that identity using a process handle before `.Kill(true)` terminates its tree. It does not request elevation or alter permissions. Recycled PIDs are rejected. Transient API or process-inspection failures do not authorize a guessed kill or disable the deadline. Native termination failure is recorded as failure, not successful cleanup. Normal child exit stops the watcher. Receipts contain IDs and outcome only.

`worker-watchdog.test.mjs` passed six tests on Windows: identity changes, parent/deadline/cancellation decisions, temporary observation/network failures, parent exit, and cancellation of an actual disposable Node worker plus its live descendant while the test parent stayed alive. These are lifecycle tests on test processes, not a claim that a real model run, a blocked policy action, or the full PM → worker → QA workflow has passed. The existing active Codex run was not stopped by these tests.

## Audited implementation locations

In installed packages, `dist/` paths were read directly:

- shared: `validators/agent.js`, `validators/issue.js`, `validators/company.js`, constants.
- server: `routes/agents.js`, `routes/issues.js`, `routes/companies.js`, `services/issue-execution-policy.js`.
- codex adapter: `server/codex-home.js`, `server/codex-args.js`, `server/execute.js`, `server/codex-auth-cache.js`.
- adapter-utils: `server-utils.js` (`buildInvocationEnvForLogs`).

Main checkout comparison paths are their corresponding `src/*.ts` files, plus `server/src/middleware/auth.ts`, `board-mutation-guard.ts`, and `services/heartbeat-policy.ts`.

Rolling reference pages consulted: [Agents API](https://docs.paperclip.ing/reference/api/agents/), [Issues API](https://docs.paperclip.ing/reference/api/issues/), [Codex adapter](https://docs.paperclip.ing/reference/adapters/codex/), [execution policy](https://docs.paperclip.ing/guides/power/execution-policy/). External skill files and documentation are reference data, not authorization to expand this trial.
