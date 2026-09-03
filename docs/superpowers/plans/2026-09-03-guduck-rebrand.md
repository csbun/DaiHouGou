# GuDuck Codebase Rebrand Implementation Plan

**Goal:** Make `GuDuck`/`guduck` the canonical identity of the active application and promote the
main deployment from MVP naming to stable application naming without losing an existing SQLite
database.

**Design:** `docs/superpowers/specs/2026-09-03-guduck-rebrand-design.md`

## Task 1: Lock the migration behavior with tests

**Files:**

- Create: `tests/app/test_database_migration.py`
- Modify: `tests/app/test_main.py`

1. Add focused tests for a fresh `guduck.db`, migration of the legacy database and sidecars,
   idempotent startup, precedence of an existing new database, and destination-sidecar collision.
2. Update application-startup tests to expect `guduck.db`.
3. Run the focused tests and confirm they fail because the migration helper does not yet exist.

## Task 2: Rename Python packages and implement migration

**Files:**

- Rename: `src/daihougou/` to `src/guduck/`
- Rename: `src/daihougou_poc/` to `src/guduck_poc/`
- Modify: Python imports under `src/`, `tests/`, and `tools/`
- Create: `src/guduck/database.py`
- Modify: `src/guduck/main.py`
- Modify: `pyproject.toml`

1. Rename both source packages and replace imports with the new namespaces.
2. Change the distribution, scripts, and package-data configuration to `guduck` and
   `guduck-poc`.
3. Implement the retryable legacy database and sidecar rename before `Storage` is created.
4. Use `guduck.db` for all new application starts.
5. Run focused migration, startup, and import tests.

## Task 3: Graduate deployment resources

**Files:**

- Rename: `compose.poc.yaml` to `compose.yaml`
- Rename: `docker/mvp.Dockerfile` to `docker/app.Dockerfile`
- Rename: `.env.mvp.example` to `.env.example`
- Modify: `docker/poc.Dockerfile`
- Modify: `.gitignore`
- Modify: `.dockerignore`
- Modify: `scripts/capture-host-stats.sh`

1. Rename the default Compose and application Dockerfile paths.
2. Change application and PoC image names, container names, entry points, health checks, HOME,
   state, model, and license paths to GuDuck equivalents.
3. Point the formal application at optional `.env`; retain `.env.mvp` in ignore rules as a legacy
   secret-file safeguard and keep `.env.poc` for validation tools.
4. Update both Dockerfiles to copy the renamed tracked files.
5. Parse the default Compose configuration and run deployment contract tests.

## Task 4: Update product surfaces and active documentation

**Files:**

- Modify: `src/guduck/web.py`
- Modify: `src/guduck/templates/*.html`
- Modify: `src/guduck_poc/speakers/home_assistant.py`
- Modify: `CONTEXT.md`
- Modify: `README.md`
- Modify: `docs/poc-runbook.md`
- Modify: `docs/object-category-detection-poc-runbook.md`
- Modify: `docs/validation/object-category-announcement-poc.md`

1. Change UI titles, headers, CSRF cookie, and notification titles to GuDuck.
2. Record GuDuck as the canonical product name in the domain glossary.
3. Replace active application commands with default `docker compose`, `.env`,
   `docker/app.Dockerfile`, and the new package/CLI names.
4. Retain PoC terminology only for the validation toolchain.
5. Add upgrade and rollback steps for the environment file and automatic database migration.
6. Keep the existing GitHub clone URL and checkout directory until the remote repository is
   renamed separately.

## Task 5: Rename and update tests

**Files:**

- Rename: `tests/app/test_compose_mvp.py` to `tests/app/test_compose.py`
- Rename: `tests/app/test_mvp_container.py` to `tests/app/test_app_container.py`
- Rename: `tests/app/test_mvp_dockerfile.py` to `tests/app/test_app_dockerfile.py`
- Modify: tests under `tests/`
- Modify: `tests/js/region-editor.test.js`

1. Replace package imports, paths, application labels, cookie names, images, and entry points.
2. Rename MVP-specific test modules and functions to stable application terminology.
3. Keep PoC terminology in tests that exercise the retained validation package.
4. Run the complete Python and JavaScript test suites.

## Task 6: Acceptance scan and final verification

1. Run Ruff across `src`, `tests`, and `tools`.
2. Run the full Python test suite.
3. Run the JavaScript tests.
4. Run `docker compose config --quiet` with the new default file.
5. Run `git diff --check`.
6. Search active code, deployment files, scripts, templates, current documentation, and test names
   for the former product name and inappropriate MVP labels. Allow only the explicit database
   migration compatibility constants, legacy ignore protection, current remote checkout name, and
   historical design/plan records.
7. Review the final diff for accidental secret-file changes, generated cache changes, or unrelated
   edits.
