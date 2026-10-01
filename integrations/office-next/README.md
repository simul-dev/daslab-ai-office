# DAS Lab Office Next — isolated integration

The owner approved Paperclip organization management + Pixel Agents office visualization + selected gstack methods on 2026-10-01. This directory implements an isolated trial. It does not replace the operating 8772/8774 services, migrate their database, repoint the public tunnel, or add a second recurring scheduler.

## Running instance

- Pixel office: `http://127.0.0.1:8790/`
- Paperclip management: `http://127.0.0.1:3101/`
- Runtime/data: `%LOCALAPPDATA%\DASLab\ai-office-next`
- Dedicated PostgreSQL: loopback `54329`, same isolated cluster under `state/instances/default/db`.
- Worker workspace: `%USERPROFILE%\AI-Office-Next\workspace`, outside Windows app-package storage.
- Knowledge snapshots: that workspace's `knowledge/`, with SHA256 and date provenance.

All three listeners are local only. Paperclip uses `local_trusted`: local HTTP callers can act as board. This is a pilot control plane, **not a security boundary against hostile same-user processes**. Never publish these ports directly or repoint the existing authenticated phone tunnel to them. Public migration needs an authenticated proxy and its own validation.

## Components

`bridge.mjs` exposes an allowlisted projection of the real company, employees, tasks, runs and comments. It checks Host and Origin, requires a UI header for mutations, and persists submission IDs to avoid duplicate tasks. No synthetic task state is used. Only a real running Paperclip run produces a working animation. A process exit is not evidence of task completion.

`pixel/` reuses the upstream renderer and licensed assets. It supports office/list views, staff selection, reporting lines, task details and an instruction form. Actual occupied floor bounds determine zoom; an empty editor grid is excluded. First setup keeps `actionsEnabled` false. After the real trial finished, the operator enabled local instruction submission; repeated setup preserves that decision.

`roles/` defines six trial staff: owner assistant, PM, researcher, developer, QA and an independent strategy/investment analyst. This trial roster does not remove the existing R&D, marketing or sales employees. Existing recurring assignments continue in the original engine until explicitly migrated without duplication.

`gstack/` installs five Codex-transformed methods into the isolated runtime and relocates the selected worker assets to `workspace/.office-methods`. Telemetry, external-model review, auto-update and external memory synchronization are disabled. Local helper tests do not prove employees used the methods; that requires run/artifact evidence.

## Windows execution

The installed `codex_local` adapter failed to create its auth-file symlink (`EPERM`) on this PC. Do not solve this by copying rotating credentials, altering the original Codex home, or disabling sandbox protections.

The supported Paperclip `process` adapter now starts `codex-worker.mjs`. The wrapper invokes the existing authenticated Codex CLI, passes role/work context on stdin, and uses a bounded workspace. It does not set CODEX_HOME or read/copy auth.json. `--ignore-user-config` avoids importing global tool configuration; the already-configured Windows `elevated` sandbox is explicitly retained alongside `workspace-write` and `approval_policy=never`. Local control-plane access needs network permission. Only an allowlist of process variables, including the run-scoped Paperclip token, enters the child process; API-provider keys are omitted. `worker-api.mjs` keeps that token out of argv and automatically includes the real run ID.

The wrapper reads its own server wake context. Review-stage participants and assigned `in_review` tasks must not be lost through an inbox shortcut; review work must not be checked out into `in_progress`. There is no persisted Codex conversation session in this mode: continuity comes from Paperclip issues/comments, workspace files and company knowledge. Token usage/cost aggregation from `codex_local` is not supplied by this generic adapter.

On this Microsoft Store installation, AppData paths resolve inside the protected package `LocalCache`. That location is not accessible to the lower-privilege sandbox account. Only trial work files/methods were copied into the ordinary user workspace; the original copies were preserved, and database/authentication files were not moved. No package-folder ACL was relaxed.

`worker-watchdog.mjs` runs separately from the wrapper and checks exact process identity, parent liveness, cancellation and a 570-second deadline. It stops only the verified worker process tree. Actual Windows tests cover cancellation, descendants, parent survival and PID-reuse protection; these are test-process results, not a claim of full production reliability. Runtime tokens are redacted even across stdout chunk boundaries before logs are sent to Paperclip.

The PostgreSQL Windows installation also needed a complete native binary tree at a shorter path; see [the database diagnosis](docs/WINDOWS-POSTGRES.md). Its actual extension functions have been checked: `levenshtein('kitten','sitting') = 3` and identical-string trigram similarity `= 1`. Normal Paperclip migrations succeeded; no migration was skipped or fabricated.

The isolated server restart preserved all employee IDs and completed task/review records. This PC's observed Paperclip startup took about 160 seconds; the launcher now allows 240 seconds with progress messages and refuses duplicate or unverified processes. The exact delay cause remains unconfirmed. The bridge keeps its shorter 30-second readiness budget. Re-running the final launcher against the healthy instances reused their verified PIDs successfully.

## Reproduction and checks

Source pins and runtime versions are recorded in `sources.json`. The installed app-local runtime contains `paperclipai@2026.916.1` and a pnpm lockfile. Existing `.codex` configuration and the original OneDrive checkout are outside the edit scope.

With the isolated dependencies prepared:

```powershell
pwsh -NoProfile -File integrations/office-next/Start-OfficeNext.ps1
node integrations/office-next/bootstrap.mjs <absolute-existing-codex.exe>
node integrations/office-next/configure-windows-workers.mjs <absolute-existing-codex.exe>
pwsh -NoProfile -File integrations/office-next/Start-OfficeNext.ps1 -BridgeOnly
node --test integrations/office-next/bridge.test.mjs integrations/office-next/context.test.mjs
```

`seed-trial.mjs` creates **one** idempotent synthetic supply-chain exercise and stores its ID in runtime `trial.json`. It intentionally starts with an unverified calculator so independent QA can find errors before developer correction and final review. It must not be represented as a customer case or a completed GIS/network-optimization product.

Do not rerun the seed to create replacement work if a run fails. Inspect the original issue and run, repair a demonstrated configuration problem within authorized boundaries, and resume that same issue. If policy blocks execution, preserve the error and stop repetitive retries. Do not turn off security policy to make the exercise pass.

## Actual trial and remaining production transition

The real PM→initial QA→developer→QA changes requested→evidence correction→QA acceptance→PM approval cycle completed on 2026-10-01. The parent and both children are done. [The worker record](docs/WORKER-RUN.md) separates actual role decisions, failed runs, operator assistance and 53 minutes 6 seconds elapsed. The developer's 34 checks, QA's independent 60 cases and four additional arithmetic comparisons passed. The root operator supplied twelve real desktop browser observations; the workers' own headless browser attempts failed. This proves an assisted execution/review cycle, not fully unattended browser QA.

[The accepted synthetic example](examples/supply-chain-trial/README.md) is preserved in this repository with source hashes and a passing relocated test run. No customer data or operating database was copied. The live demo route serves exactly the configured in-workspace HTML in a sandboxed response without network or same-origin privileges.

Subsequent work must connect existing employee identities/history, recurring duties and phone authentication, and provide a reliable browser verifier for workers. Production replacement is not accomplished by this trial UI or by editing role text. The original authenticated phone office continues to use its existing services and data.

Primary references: [Paperclip](https://github.com/paperclipai/paperclip), [Pixel Agents](https://github.com/pablodelucca/pixel-agents), [gstack](https://github.com/garrytan/gstack), [Codex Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox).
