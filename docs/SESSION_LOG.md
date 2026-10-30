# Session log

## 2026-10-29 — Three activity classes and unclaimed music

- Implemented `private/codex_prompt_three_class.md`: music/speech/silence labels,
  speech threshold 0.40, relative RMS silence gate with flat-memo fallback,
  bounded sound-island voting before the existing mode filter, music take cutting,
  and a separate unlabeled bin. Kept all existing module responsibilities and
  placed unclaimed-music selection in `keeper/unlabeled.py`. No dependencies added.
- AST and cached-score processing retain all existing columns and add per-window
  `music = max(singing, instrument)`, averaged into frame and region display scores.
  Audacity import maps both legacy activity labels to music; evaluation uses the
  shared three-class list to produce a 3x3 matrix without changes to its algorithm.
- Added `--rms-json`, `--no-export`, and `--duration-s`; copied real score/truth
  files and generated rounded numeric RMS in `tests/fixtures/test_memo/`. The truth
  file was initially absent, then became available after the user requested a
  second check; the fixture uses the actual file without reconstructing intervals.
  Printed speech regions before keying mock commands at 8.0, 21.0, and 27.0 seconds.
- Changed files: `keeper/{types,config,segment_ast,frames,cut,pipeline}.py`, new
  `keeper/unlabeled.py`, `eval/audacity_labels.py`, migrated
  `tests/test_{audacity_labels,cut,frames,pipeline_mock,regions}.py`, new
  `tests/test_{silence_gate,island_vote,unlabeled,real_memo}.py`, `tests/conftest.py`,
  `pytest.ini`, all six fixture files (five data files plus README), `README.md`,
  and this log. `keeper/regions.py`, `keeper/songmap.py`, and `eval/evaluate.py`
  already consume the shared labels or generic records and required no edits.
- Validation: `.\.venv\Scripts\python.exe -m pytest -s` — **61 passed**,
  three existing `audioread` deprecation warnings, on Python 3.11.9. Every test
  blocks socket connection entry points. `pytest.ini` scopes default discovery
  to automated `tests/`, excluding manual API smoke scripts under `scripts/`.
  The first run exposed two migrated expectation errors and a floating-point
  exact-gap comparison; corrected them and reran the full suite successfully.
  `git diff --check` passed. Used the venv outside the sandbox because the
  sandbox cannot access its base Python executable.
- Real memo results: **49/57 frames correct, 0.8596491228 accuracy**. Recall:
  music **1.0**, speech **1.0**, silence **0.5555555556 (10/18)**. Chosen gate
  **-45.26 dB** (10th percentile -57.26 dB, median -16.8 dB). Three chorus takes:
  **A 0.00–8.00 s**, **B 10.20–21.00 s**, **C 22.70–27.00 s**; zero unlabeled
  takes, zero warnings. Every boundary is within the requested 0.75-second tolerance.
- Verified the standalone CLI prints the same summary and writes metadata without
  exporting WAV files:

  ```powershell
  .\.venv\Scripts\python.exe -m keeper.pipeline tests/fixtures/test_memo --scores-csv tests/fixtures/test_memo/window_scores.csv --rms-json tests/fixtures/test_memo/rms_db.json --no-export --parser mock --mock-labels tests/fixtures/test_memo/mock_commands.json --out outputs/three_class_fixture
  ```

### Decisions

1. `frame_labels_from_scores` returns `(labels, gate_db)`. `regions.json` is now an
   object with top-level `silence_gate_db` and `regions` fields; its former root
   array is the value of `regions`.
2. Numeric-only mode requires cached scores, the mock parser, and no export.
   The parser receives an empty mono float32 array because it uses timestamps.
   The fixture directory is recorded as `source_file`. Duration is read from
   `meta.json`, unless explicitly overridden with `--duration-s`.
3. The source audio lasts 28.579125 seconds, while the requested truth duration
   is 28.28. Preserve all 58 rounded RMS values, use the first 57 for the declared
   duration (including gate estimation), and clamp the final region. Reject
   nonfinite RMS, invalid durations, or insufficient frame coverage.
4. Compute music maxima before overlapping-window averaging, including for legacy
   cached CSVs that lack the new column; retain raw/group columns throughout.
5. Empty RMS uses the absolute fallback. Config validation requires a winning
   island share greater than 0.5 and at most 1, avoiding ambiguous tied winners.
   Island duration uses frame count times hop, including a full grid cell for a
   partial final frame; the existing mode filter and short-region absorption
   retain their previous behavior after island voting.
6. Unlabeled rolls may enter neighboring silence but stop at its far boundary
   or an adjacent speech boundary, and stay within the memo. Check command overlap
   using the rolled candidate and drop it wholesale on any positive overlap;
   touching endpoints are allowed. The minimum counts music duration only.
   Reuse the existing song-map naming path with non-command metadata assigned to
   the new unlabeled section. Variants follow chronological order.
7. Compare a silence end against `start + max_gap_s` to preserve the strict gap
   rule at decimal boundaries without subtraction roundoff. In the hand timeline,
   the former 17–20-second instrument region is now music, so chorus B begins at
   17.0 seconds; the 42–60-second region is an unlabeled take.
8. Default pytest discovery targets `tests/` so a full run remains offline instead
   of importing the manual API smoke scripts. A shared autouse fixture forbids
   socket connections throughout the automated suite. No API/UI/hosting work.

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
