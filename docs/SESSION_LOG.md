# Session log

## 2026-10-28 — Boundary regression tests

- Read `AGENTS.md` and the implementation and existing tests. Added six regression
  tests without changing implementation code or dependencies:
  - Singing starting at exactly 0.0 retains that start and ends at the command.
  - A command ending exactly at the memo end selects the correct preceding take.
  - A command at the memo start produces zero takes and exactly one warning,
    even when singing follows it.
  - Singing shorter than pre-roll plus post-roll stays between neighboring
    commands, with no overlapping takes.
  - A synthetic 1.5-second memo runs through the pipeline with an injected scorer
    and produces finite, contiguous regions bounded by the exact memo duration.
  - A 0.01-second take exports as a readable 160-sample, mono, 16 kHz PCM-16 WAV
    with overlapping fades, preserving the input audio and song map.
- Validation: `.\.venv\Scripts\python.exe -m pytest tests` — **43 passed** in
  17.94 seconds, with three `audioread` dependency deprecation warnings (`aifc`,
  `audioop`, and `sunau`). No test failures occurred; no implementation fixes
  were needed. The first invocation could not access the base Python executable
  inside the sandbox; the same command succeeded outside the sandbox on Python
  3.11.9.
- Ran the entire automated suite in `tests/`. Excluded the pre-existing,
  untracked `scripts/test_baseten.py` and `scripts/test_spark_audio.py` manual
  smoke scripts because importing them invokes an external API and local audio.
  Left all pre-existing working-tree changes untouched.
