# GuDuck Codebase Rebrand and Application Graduation

## Status

Approved in chat on 2026-09-03.

## Context

The application has adopted **GuDuck** as its product name. The repository still exposes the
former name through Python packages, command-line entry points, Docker resources, runtime paths,
browser state, tests, and operational documentation. The primary application is also still labeled
as an MVP even though it is now the supported application path.

This change makes GuDuck the canonical identity throughout active code and promotes the primary
application from MVP naming to a stable application surface. It does not assert that the service is
safe for exposure to the public internet. The existing trusted-local-network boundary and warning
about the unauthenticated management interface remain in force.

## Canonical Vocabulary

| Concept | Canonical name |
| --- | --- |
| Product and UI name | `GuDuck` |
| Python distribution | `guduck` |
| Primary Python package | `guduck` |
| Primary CLI | `guduck` |
| Device-validation package | `guduck_poc` |
| Device-validation CLI | `guduck-poc` |
| Compose file | `compose.yaml` |
| Application Dockerfile | `docker/app.Dockerfile` |
| Application image | `guduck:local` |
| Application environment file | `.env` created from `.env.example` |
| Application database | `guduck.db` |
| Application state root | `/var/lib/guduck` |
| Bundled application root | `/opt/guduck` |

The term **PoC** remains valid only for device, speaker, and model validation tools. The production
application, its Docker image, environment template, tests, and operational instructions must not be
described as an MVP.

## Goals

- Rename all active Python namespaces, entry points, deployment resources, runtime paths, browser
  state, user-facing product text, and their tests from the former identity to GuDuck.
- Replace MVP-specific deployment naming with stable application naming.
- Preserve existing camera configuration, rule configuration, and event history by migrating the
  legacy SQLite database before the new application opens it.
- Keep the PoC toolchain available under the GuDuck identity for future device and model validation.
- Keep current operational documentation executable after the file and command renames.

## Non-goals

- Renaming the GitHub repository, its remote URL, or the developer's checkout directory.
- Providing compatibility imports for the former Python package names.
- Providing aliases for the former command-line entry points or Docker resource names.
- Rewriting historical design specifications and implementation plans. Those files describe the
  repository state at the time they were written.
- Declaring the unauthenticated local management interface suitable for internet exposure.
- Changing application behavior, detection policy, speaker behavior, or persistence schema.
- Advancing the semantic version solely because the product name changed.

## Package and Interface Rename

The source directories become `src/guduck` and `src/guduck_poc`. All imports in application code,
tools, and tests change to the new namespaces. The distribution name in `pyproject.toml` becomes
`guduck`; its two scripts become `guduck` and `guduck-poc`. Package-data configuration follows the
new primary package so templates and static files remain installed.

No compatibility modules are retained. A stale editable installation must be refreshed after the
rename, and the documented developer workflow will make that explicit. Generated caches,
`*.egg-info`, and virtual-environment scripts are not source artifacts and are not manually renamed.

The FastAPI title, page header and titles, Home Assistant notification title, and CSRF cookie become
`GuDuck` or `guduck_csrf` as appropriate. Existing CSRF cookies will be discarded naturally; they do
not contain durable user data.

## Deployment Graduation

The default deployment moves from `compose.poc.yaml` to `compose.yaml`, allowing ordinary
`docker compose` commands without a file override. The primary Dockerfile becomes
`docker/app.Dockerfile`; its image is `guduck:local`, its entry point is `guduck`, and its runtime
paths use `/var/lib/guduck` and `/opt/guduck`.

The application configuration template becomes `.env.example`, and Compose reads an optional
`.env`. Existing `.env.mvp` and `.env.poc` files may contain credentials and must not be read,
rewritten, or moved by this change. Legacy secret-file patterns remain ignored so an existing local
file cannot be committed accidentally. The upgrade instructions tell operators to stop the old app
and create `.env` from their existing values.

The `probe` Compose profile, `.env.poc`, `docker/poc.Dockerfile`, validation configuration, and PoC
runbooks remain. Their package, CLI, image, container paths, and references adopt the GuDuck name,
but their PoC terminology remains because it describes their real role.

## Database Migration

The new application opens `<DATA_DIR>/guduck.db`. Before creating `Storage`, startup runs a focused
legacy-file migration in the configured data directory:

1. Create the configured data directory if necessary.
2. If `guduck.db` already exists, use it and do not modify any legacy files.
3. If `guduck.db` is absent and the legacy database exists, require the old application to be
   stopped, rename any existing `-wal` and `-shm` sidecars to the corresponding GuDuck names, then
   atomically rename the main database file in the same directory.
4. If a destination sidecar already exists while its legacy counterpart also exists, fail with a
   clear migration error rather than overwriting either file.
5. Log a successful migration without exposing paths containing credentials or device data.

Renaming sidecars before the main file makes an interrupted migration retryable: until the final
main-file rename succeeds, startup still sees the legacy database and can finish any remaining
sidecar moves. The migration does not modify SQLite contents or schema version.

Tests cover a fresh installation, successful migration with retained data, sidecar migration,
idempotent startup, precedence of an existing `guduck.db`, and collision failure.

## Documentation Policy

`README.md`, `CONTEXT.md`, active runbooks, example configuration, and operational scripts are
updated to the new commands and paths. The README continues to use the current GitHub clone URL and
checkout directory until the remote repository is renamed separately.

Historical files under `docs/superpowers/specs/` and `docs/superpowers/plans/` remain unchanged,
apart from this design and its implementation plan. They may contain the former names because they
are records, not current operating instructions. Searches used for acceptance therefore exclude
historical documents and the narrow legacy database migration compatibility code.

`CONTEXT.md` records GuDuck as the product name. No domain concepts for camera rules, detections,
announcements, or validation data change.

## Verification

The completed change must pass:

- the complete Python test suite;
- Ruff checks for source, tests, and tools;
- JavaScript tests for the region editor;
- Compose configuration parsing with the new default file;
- Dockerfile and Compose contract tests under their new filenames;
- focused database migration tests;
- a search proving that active packages, configuration, templates, scripts, and current docs do not
  expose the former name or MVP label outside the documented migration exception;
- `git diff --check`.

Container image builds may still require network access to download packages and pinned models. If a
full image build cannot run, static Dockerfile tests and Compose parsing remain mandatory, and the
unexecuted build must be reported.

## Rollout

Operators upgrade by stopping the former application container, creating `.env` from their existing
private configuration without committing it, rebuilding with `docker compose build app`, and
starting `docker compose up -d go2rtc app`. The bind-mounted host state directory remains
`deploy/app/state`, so startup can find and migrate the legacy database automatically.

Rollback after the database filename migration requires stopping GuDuck and renaming the database
and any sidecars back before starting an older image. The runbook will state this explicitly.
