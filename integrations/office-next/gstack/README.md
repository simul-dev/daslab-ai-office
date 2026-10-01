# gstack — isolated AI Office methodologies

Prepared 2026-10-01 from official `https://github.com/garrytan/gstack`, MIT license,
commit `96764e80a641e28141ec8297223768029f5bf483` (upstream v1.91.9.0).
The vendor checkout is detached at that commit. No global Codex skill, global
configuration, login or credential was created/copied by this installer.

## Installed scope

`office-hours`, `plan-ceo-review`, `plan-eng-review`, `review`, `retro` are installed
under `%LOCALAPPDATA%/DASLab/ai-office-next/skills/gstack-*/SKILL.md` for explicit
loading by employee role instructions. They are generated with upstream's
**Codex host renderer**, rather than copying Claude skill files into Codex.

`roles/common.md` and the six individual role instructions define how employees
use these methods: find existing evidence first, make routine decisions locally,
send material blockers to PM, and require real artifacts and independent QA.
The investment analyst reports separately to the owner.

The full gstack browser/CSO/deployment stack is outside this installation. The
selected methods' bin/lib/docs/template support is retained, and fallback
section references point to the corresponding inlined Codex document. Browser
and design binary paths are explicitly unavailable. Workflows requiring them
must use the office's supplied tools or report what remains unverified.

## Local adjustments

`prepare.mjs` makes these reproducible adjustments to generated output, keeping
the immutable source and MIT license alongside it:

- Resolve runtime and state to the private `ai-office-next` directory. No global
  `HOME` or `CODEX_HOME` rewrite; shell helpers source `gstack-runtime/env.sh`.
- Set `telemetry: off`, `auto_upgrade: false`, `update_check: false`,
  `artifacts_sync_mode: off`, `codex_reviews: disabled`; no outside Claude/API.
- Disable the outside-review launchers and global session-discovery helper with
  exit 78. The local role contract also prohibits nested model CLI invocations.
  These are deployment constraints, not a complete OS security sandbox.
- Skip unrelated global Claude/GBrain profile discovery in the preamble.
- Mark office workers as `spawned`; unresolved owner decisions are sent through
  the existing issue/PM workflow. Automatic choices never create new authority.
- Preserve official source sections as references; Codex already inlines their
  resolved content, so installed fallback section files redirect to that content.

## Reproduction

1. Clone the official repository to
   `C:/Users/User/AppData/Local/DASLab/ai-office-next/vendor/gstack` and detach at
   the pinned commit above. Do not execute upstream `setup` or team-mode hooks.
2. Use the system `skill-installer` helper with the pinned `--ref`, paths
   `office-hours plan-ceo-review plan-eng-review review retro`, and an explicit
   `--dest` staging directory **inside this worktree's ignored test-results**.
   Then use native PowerShell `Copy-Item` to place that staging directory at
   `ai-office-next/gstack-source-skills`. The installer refuses existing targets.
   The generator verifies selected SKILL.md against the checkout, normalizing
   CRLF only.
3. Use portable Bun 1.4.2, official archive
   `https://github.com/oven-sh/bun/releases/download/bun-v1.4.2/bun-windows-x64.zip`.
   Verified archive SHA-256:
   `ce4c17497b2f29712a99d3d53f028de28cd42e3bacb8589599e7f000e49b6405`.
   Extract under `ai-office-next/tools/bun-v1.4.2`; no global PATH mutation.
4. Run the following from PowerShell with the bundled Node or portable Bun:

```powershell
$officeNextRoot = 'C:\Users\User\AppData\Local\DASLab\ai-office-next'
$gstackBun = Join-Path $officeNextRoot 'tools\bun-v1.4.2\bun-windows-x64\bun.exe'
$gstackIntegration = 'C:\Users\User\.codex\worktrees\pm-ready\daslab-ai-office\integrations\office-next\gstack'
& $gstackBun (Join-Path $gstackIntegration 'prepare.mjs') --source (Join-Path $officeNextRoot 'vendor\gstack') --root $officeNextRoot --bun $gstackBun
& $gstackBun (Join-Path $gstackIntegration 'verify.mjs') $officeNextRoot
```

The explicit Bun generator entry point avoids an observed Windows issue where
running upstream `scripts/gen-skill-docs.ts` directly returned zero without
generating files. Generation now checks its expected output.

Windows Store Python and the packaged bundled Python resolved writes under
LocalAppData to package `LocalCache` paths on this host. Staging outside
LocalAppData and copying with native PowerShell avoids reporting an invisible
or wrong-path installation as successful. The active application Python runtime
is unaffected.

## Actual verification

`verify.mjs` checks each selected Codex skill, resolved helper/reference files,
disabled options and outside launchers. It creates an isolated empty Git project,
runs the real preamble, persists a decision with the upstream helper, reads that
decision back, and finishes the local skill lifecycle. It invokes **zero models**.

2026-10-01 result: five skill/reference checks passed; preamble returned spawned,
telemetry off, updates false and sync off; durable decision write/read passed;
outside helpers returned blocked. Evidence is
`ai-office-next/verification/gstack-check.json`, and installation provenance is
`ai-office-next/gstack-install.json`.

This proves selected installation and local helper behavior. It does not prove
that an employee used the methodology well, that a real project passed review,
or that the business achieved its goal. Those require actual Paperclip task runs
and artifact verification. Existing phone dictation validation remains PENDING.

## Accessible worker workspace

The real Windows sandbox worker could not traverse Codex's package LocalCache
directory. The trial workload was therefore placed at
`C:/Users/User/AI-Office-Next/workspace`, keeping the existing stronger sandbox,
ACLs, database and authentication unchanged. `relocate-gstack.mjs` copies only
the five installed skills, their helper assets, and the already verified
portable Bun into that workspace's `.office-methods` directory.

```powershell
& $gstackBun (Join-Path $gstackIntegration 'relocate-gstack.mjs') --source-root $officeNextRoot --workspace 'C:\Users\User\AI-Office-Next\workspace'
& $gstackBun (Join-Path $gstackIntegration 'verify.mjs') 'C:\Users\User\AI-Office-Next\workspace\.office-methods'
```

All installed method, runtime and state references are rewritten to
`.office-methods`; the source installation is hash-checked as unchanged. State
starts with the disabled-service configuration, without importing existing logs,
decisions or private state. `gstack-relocation.json` retains original provenance
and records the portable Bun source/destination hash. Company role files are
copied to `workspace/roles` with the same path update and snapshot-first wording.

Actual verification at the new location passed again: skill references,
spawned preamble, decision persistence/recall and disabled outside helpers.
This local helper check is separate from the subsequently resumed real QA task.
