# Windows PostgreSQL preparation for the isolated Paperclip trial

Checked on 2026-10-01 (Asia/Seoul). This note concerns only the `ai-office-next` experiment. It does not change the existing AI Office data, ports 8772/8774, or its scheduler.

## Pinned software and observed failure

- Paperclip runtime: `paperclipai@2026.916.1`.
- Native database package: `@embedded-postgres/windows-x64@18.1.0-beta.16`, loaded by `embedded-postgres@18.1.0-beta.16`.
- Trial database directory: `%LOCALAPPDATA%\DASLab\ai-office-next\state\instances\default\db`.
- Trial database port: `127.0.0.1:54329`; Paperclip HTTP port: `127.0.0.1:3101`.

The installed Paperclip migrations require real PostgreSQL extensions: `0051_young_korg.sql` creates `pg_trgm`, and `0080_company_search_fuzzystrmatch.sql` creates `fuzzystrmatch`. Embedded startup failed with extension-not-available errors even though the native package contained their control files, SQL scripts and DLLs.

The embedded process's `postmaster.opts` recorded a much longer executable path under the Codex Store package's `LocalCache\Local` directory. Starting the same native PostgreSQL through the shorter literal AppData path allowed the main operator to create both extensions normally. This is evidence of a native path/environment problem, not missing extension packages. Path-length or relocation behavior is a likely explanation; a controlled test has not isolated the exact cause. The embedded wrapper also replaces the child environment with a single locale setting, so a path-only cause must not be stated as proven.

## Supported connection mode

The installed stable package supports this configuration shape:

```json
{
  "database": {
    "mode": "postgres",
    "connectionString": "<local PostgreSQL connection string kept outside Git>"
  }
}
```

`DATABASE_URL` takes precedence over the configured connection string. Do not set both accidentally. A `postgres` mode without a connection string falls back to embedded PostgreSQL, so mode alone does not select the external database.

The external database branch runs normal migration inspection and application, then creates the database connection pool. It does not import/start the embedded PostgreSQL wrapper. Noninteractive startup applies pending migrations unless configuration disables the prompt path; `PAPERCLIP_MIGRATION_AUTO_APPLY=true` is an explicit supported choice for this isolated installation. Do not alter migrations, fabricate extension records, or skip schema checks to make startup pass.

There is no supported executable-path environment override in the installed `embedded-postgres/dist/binary.js`: it imports a platform package, whose exports calculate native executable paths relative to that package.

## Local preparation chosen for this trial

The main operator prepared a short native directory:

```text
%LOCALAPPDATA%\DASLab\ai-office-next\postgres-native
```

The entire official native directory is copied together, preserving `bin`, `lib`, `share`, and their relative layout. This is an isolated local packaging accommodation, not a change to PostgreSQL or Paperclip source. Copying only the executable or only extension control files is insufficient. It does not copy the database, Codex credentials, or production data.

Read-only checks confirmed matching SHA-256 hashes between the installed package and the short copy for eight key files: `postgres.exe`, `pg_ctl.exe`, both extension DLLs, both control files, and the fuzzystrmatch base/update SQL scripts. This is a sample integrity check, not a claim that every file in the directory was independently hashed.

The trial launcher should:

1. Preserve the existing trial database directory. Verify the process using port 54329 belongs to that directory before reusing it; refuse another instance instead of switching ports silently.
2. Start the short-path native PostgreSQL with loopback-only listening, the existing trial database directory and the fixed trial port. Launch background processes with hidden windows.
3. Confirm database readiness and directory identity before starting Paperclip with external PostgreSQL mode.
4. Let the installed Paperclip migrations run normally. Record schema/startup results separately from HTTP availability.
5. Manage the lifetime of this specific PostgreSQL process. Paperclip does not stop an externally managed PostgreSQL server when it exits. Shutdown must validate executable/PID/data directory and must not affect production processes.

Keep the real database connection string in the protected local configuration or process environment, never in command arguments, reports, tracked files, or printed configuration dumps. The launcher must also fail if a conflicting inherited `DATABASE_URL` points elsewhere.

## Verification before calling the database ready

Using a local database client with credentials kept out of logs, verify:

```sql
SHOW data_directory;
SHOW listen_addresses;
SELECT name, installed_version
FROM pg_available_extensions
WHERE name IN ('pg_trgm', 'fuzzystrmatch');
SELECT levenshtein('kitten', 'sitting') = 3 AS fuzzystrmatch_works;
SELECT similarity('paperclip', 'paperclip') = 1 AS pg_trgm_works;
```

Extension installation success alone does not verify that the relocated DLL can execute. The function checks do. Confirm migration completion and a healthy Paperclip API after these checks. Then test a stop/start cycle through the actual trial launcher; a manually started probe does not prove restart reliability.

At the time this note was written, the short-copy file checks and the main operator's original-path extension creation were confirmed. The final launcher restart, short-copy function checks, and Paperclip business workflow are separate evidence to record after they pass.

## Sources

- Actual installed `@paperclipai/shared/dist/config-schema.js`: `database.mode`, `connectionString`, embedded data/port fields.
- Actual installed `@paperclipai/server/dist/config.js`: `DATABASE_URL` precedence; `dist/index.js`: migration handling, external database branch, and shutdown ownership.
- Actual installed `embedded-postgres/dist/binary.js`, `dist/index.js`, and `@embedded-postgres/windows-x64/dist/index.js`: native path selection and spawn behavior.
- [Paperclip database documentation at the inspected source commit](https://github.com/paperclipai/paperclip/blob/467125fafb47a8520856504fecc48d6e32055db1/docs/deploy/database.md). Rolling source can differ from the installed stable version; runtime behavior above was checked in the installation.
- [embedded-postgres upstream](https://github.com/leinelissen/embedded-postgres).
- [PostgreSQL 18 extension packaging](https://www.postgresql.org/docs/18/extend-extensions.html): extensions require control/SQL files and, for native functions, a shared library; these files must remain available on the server.
