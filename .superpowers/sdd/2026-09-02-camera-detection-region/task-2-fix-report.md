# Task 2 Fix Report

Status: complete.

Fix: moved `FfmpegFrameSource.region` after all pre-existing optional constructor parameters, preserving legacy positional calls. Added a regression test covering positional process factory, sleeper, and watchdog arguments.

Verification: `python3 -m py_compile` and `git diff --check` pass. Pytest and runtime dependencies remain unavailable in this environment.
