# Task 2 Report

Status: complete.

Commit: `feat: crop camera frames to detection regions` (final commit created in this worktree).

Changes:

- Added optional `DetectionRegion` support to `build_ffmpeg_command()` and `FfmpegFrameSource`.
- Added crop-first FFmpeg filter generation with six-decimal normalized expressions.
- Preserved the existing full-frame filter and frame-source behavior.
- Added command, real-FFmpeg crop, and `SceneChangeGate` regression tests.

Verification:

- `python3 -m py_compile ...`: passed.
- `pytest tests/app/vision/test_frame_source.py -v`: unavailable (`pytest` is not installed).
- Real-FFmpeg tests were not runnable because the runtime dependencies are unavailable; they are guarded to skip when FFmpeg is absent.

Concerns: full pytest and FFmpeg execution should be run in the project development environment before merge.
