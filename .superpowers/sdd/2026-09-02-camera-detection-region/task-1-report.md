# Task 1 Report

## Implementation Summary

- Added immutable `DetectionRegion` value object with six-decimal normalization, finite/bounds validation, stable `invalid detection region` errors, and `FULL_FRAME_REGION`.
- Added `CameraConfig.detection_region` and storage read/write support.
- Added SQLite v3 creation schema with four region columns.
- Added transactional exact v2-to-v3 migration, preserving existing camera, rule, configuration, and event rows.
- Added strict malformed-schema rejection before migration changes are applied.

## TDD Commands and Outputs

RED command (required):

```text
pytest tests/app/test_detection_region.py -v
```

Output:

```text
zsh:1: command not found: pytest
```

Focused RED/GREEN command (required):

```text
pytest tests/app/test_storage.py tests/app/test_detection_region.py -v
```

Output:

```text
zsh:1: command not found: pytest
```

The bundled Python 3.12 interpreter is available, but it also has no pytest module:

```text
/opt/homebrew/bin/python3.12 -m pytest tests/app/test_detection_region.py tests/app/test_storage.py -v
/opt/homebrew/opt/python@3.12/bin/python3.12: No module named pytest
```

Available verification:

```text
PYTHONPATH=src /opt/homebrew/bin/python3.12 <direct value-object/storage smoke checks>
smoke checks passed

PYTHONPATH=src /opt/homebrew/bin/python3.12 <direct v2 migration smoke check>
migration smoke passed

/opt/homebrew/bin/python3.12 -m compileall -q src tests
git diff --check
```

## Files Changed

- `src/daihougou/detection_region.py`
- `src/daihougou/storage.py`
- `tests/app/test_detection_region.py`
- `tests/app/test_storage.py`

## Self-Review

- Region values are rounded before validation and fields remain frozen after construction.
- Full-frame detection uses value equality and does not depend on constant initialization order.
- v2 migration checks the exact table set and original camera columns before issuing four `ALTER TABLE` statements in one connection transaction.
- `PRAGMA user_version = 3` is issued only after all region columns are added.
- Unknown camera updates enforce the exact one-row update contract.
- Existing welcome phrase upgrade and built-in rule backfill remain after schema resolution.

## Concerns

- The repository environment lacks pytest, so the requested pytest suite and complete suite could not be executed. No dependencies were installed.

## Round 1 Fix

- Wrapped all four v2 migration `ALTER TABLE` statements and `PRAGMA user_version = 3` in an explicit transaction with rollback on any exception.
- Added exact normalized `sqlite_master` definition checks for `cameras`, `camera_rules`, `rule_configs`, and `events` in both v2 and v3 validation paths.
- Added regression coverage for malformed non-camera v2 schemas; such databases are rejected before region columns are added.

Verification after fix:

```text
PYTHONPATH=src /opt/homebrew/bin/python3.12 <v2 migration smoke check>
migration smoke passed

/opt/homebrew/bin/python3.12 -m compileall -q src tests
git diff --check
```

`pytest` remains unavailable in the repository environment (`No module named pytest`); no dependencies were installed.
