# Office Next: Pixel Agents presentation

This isolated UI builds the real Pixel Agents `OfficeCanvas`, `OfficeState`,
pathfinding and character renderer from the reviewed source pinned in
`upstream.json`. No upstream source patch is needed. It can run behind the
existing authenticated office gateway as well as the standalone loopback
bridge. It does not replace the legacy database or authentication mechanism.

## Build

Set `PIXEL_AGENTS_VENDOR` to an existing checkout of the pinned repository when
it is not in the default `%LOCALAPPDATA%/DASLab/ai-office-next/vendor/pixel-agents`
directory. Then run `pnpm install --frozen-lockfile --ignore-scripts`, `pnpm test`
and `pnpm build` in this directory. Build checks the source commit and decodes
the actual bundled sprites into `dist/pixel-assets.json`.

The integration bridge serves `dist/` on its own loopback address. Vite dev mode
can proxy `/api/office` through `OFFICE_BRIDGE_URL` (default
`http://127.0.0.1:8790`). The bridge's same-origin protection means production
build + bridge is the primary verification surface; do not relax the bridge to
expose the unauthenticated development UI.

## Data boundary

- `GET /api/office/snapshot` provides `observedAt`, `company`, `agents`, `issues`
  and `capabilities.actionsAvailable`. Agents have stable string identity,
  name, role, status, managerId, currentIssue, activeRun, blockedReason and
  lastResult. `paperclipUrl`, `legacyUrl` and `notice` are optional.
- Work animation is enabled only by an observed running/busy/working state or
  an actual running run. A task marked `in_progress` is not sufficient. An
  assigned task without a running worker is labelled as waiting for execution.
- Polling failure preserves the last recorded text with an explicit stale
  notice and disables working animation and command submission. It does not
  invent progress, completion percentages or fictional tool activity.
- `POST /api/office/issues` uses `{agentId, instruction, requestId}` and
  `X-Office-Next: 1` and `X-DAS-Office: 1`. Buttons are disabled during submission. A failed request
  with unchanged contents keeps the same idempotency request ID on retry.
- Direct assignments through this bridge to the configured developer attach
  Paperclip's QA review followed by PM approval, require comments, allow two
  review rounds and name `local-board` as responsible human. The three configured
  participants must be distinct members of the same company; unavailable or
  unapproved QA/PM participants cause a 409 response rather than an unreviewed
  assignment. Other simple/status roles keep their existing behavior. This
  narrow bridge rule does not enforce a policy on every future child issue
  created directly by a PM through Paperclip.
- `GET /api/office/issues/:id` supplies the issue and actual comments. Stored
  text is rendered through React text nodes, not raw HTML.

Office seating and idle walking are visual affordances only; they do not
represent actual agent-to-agent messages. Clickable staff labels and cards
show the actual task, reporting manager and last recorded result. Choosing a
person or changing a view never launches an agent.

The upstream terminal/settings transport is replaced at build time with a
presentation-only no-op module. This app does not start Claude, install hooks,
scan other sessions, grant permissions, write global settings or run the
upstream standalone server. Task creation is owned only by the Paperclip bridge.

## Authentication and phone connection

The UI checks same-origin `/api/auth` before reading private work. A missing
endpoint is accepted only on loopback for the standalone bridge. Public 401s
stop polling and submission; clicking the login button preserves only the
current recipient, draft and matching retry ID in per-tab session storage for
up to 24 hours. Restoration never submits automatically. Explicit logout clears
that draft after `/api/auth` confirms the device is unauthenticated, including
the host's normal POST → 303 → HTML login response.

The new header exposes phone connection only for local authenticated mode with
`pairing_available: true`, no read-only restriction, and an exact HTTPS public
origin. Clicking it explicitly starts `/api/owner/pair/start`; no code is issued
on page load. A code must target exactly the configured origin's `/login` with
a single expected fragment. The existing host's MIT QR generator is loaded
on demand. Code material exists only in memory and the open dialog.

Closing, expiry, late responses and retry are serialized with
`/api/owner/pair/cancel`. A fresh issuance cannot overtake cancellation. Failed
cancellation removes the visible code and must be retried before a new code is
issued. A phone scan does not automatically close the QR, because the UI has no
verified consumption event. The operator closes it after connecting the phone.

The shell uses actual project DAS Lab wordmarks and the navy/cyan/cool-white
palette from `knowledge/office-brand.md`. Pixel engine layout and sprites are
unchanged. Wordmark source and separate company-asset status are recorded in
`public/brand/README.md`.

## License and verification

Code and bundled source assets use upstream MIT terms. Character provenance
and CC0 source are recorded in `THIRD-PARTY-NOTICES.txt`; the full upstream MIT
notice is copied into every built distribution. No paid external sprite pack
is used.

`tests/snapshot.test.mjs` checks task-vs-execution semantics, disconnection,
blockers, duplicate identity, stable character mapping and safe result links.
Passing these tests and building the bundle does not prove real employee
completion, browser behavior or phone usability; those require the live
bridge and browser checks.
