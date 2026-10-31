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

## 2026-10-30 - Sparse truth and take boundary evaluation

- Read `AGENTS.md`. Updated `eval/audacity_labels.py` documentation and
  `eval/evaluate.py` so leading, internal, and trailing truth gaps count as
  silence over the prediction's memo duration. Empty activity truth is all
  silence. Predictions still require continuous coverage; overlapping activity
  truth and truth activity beyond the memo duration remain errors.
- Audacity import preserves `cmd:` command truth and `take:` boundary truth,
  including point command annotations. Neither annotation type affects frame
  labels or the duration used for frame scoring. Legacy singing/instrument
  labels still map to music.
- Added `take_boundary_error(pred_songmap, truth_takes)`, accepting a `SongMap`
  and either `Take` records or raw Audacity rows (selecting only `take:` rows).
  Each truth independently matches its greatest positive temporal overlap;
  ties follow song-map order and a prediction may match multiple truth takes.
  Results contain `per_take` rows with `truth_label`, `predicted_id`, and
  absolute `start_error_s`/`end_error_s`, plus `misses` and `extras` counts.
  Misses have null prediction IDs and errors; extras are unused predictions.
  Touching endpoints do not match. Invalid take intervals raise `ValueError`.
- Added regression coverage in `tests/test_audacity_labels.py` for sparse and
  annotation-only truth, preserved annotation text, overlapping activity,
  prediction gaps, maximum-overlap matching across sections, absolute boundary
  errors, deterministic ties, shared predictions, empty inputs, misses, extras,
  touching endpoints, and invalid take intervals. No dependencies added.
- Validation: `.\.venv\Scripts\python.exe -m pytest tests/test_audacity_labels.py`
  passed **21 tests**; `.\.venv\Scripts\python.exe -m pytest` passed **77 tests**
  in 15.85 seconds on Python 3.11.9, with the three existing `audioread`
  deprecation warnings. `git diff --check` passed.

## 2026-10-30 - Local FastAPI UI skeleton

- Executed `private/codex_prompt_ui_skeleton.md` after reading `AGENTS.md`.
  Added `app/__init__.py`, `app/server.py`, `app/static/index.html`,
  `scripts/make_demo_memo.py`, `tests/test_server.py`, and `tests/test_demo_memo.py`.
  Updated `requirements.txt`, `README.md`, and this log. No changes to `keeper/`
  or the preceding evaluation work; no Muse Spark, Baseten, or Modal integration.
- Added and installed only the requested direct web dependencies: FastAPI for
  API routing, Uvicorn for serving the app, and python-multipart for file uploads
  (plus their normal transitive dependencies). Installed versions were FastAPI
  0.162.0, Uvicorn 0.54.0, and python-multipart 0.0.32.
- Implemented sorted memo listing, detail JSON with flattened regions and region-
  derived duration, guarded WAV serving, synchronous multipart upload processing,
  and static page serving. Settings hold repo-relative default paths. Existing
  absolute exporter paths are normalized to relative clip paths and encoded URLs
  in the API without modifying pipeline outputs. Resolved-path containment rejects
  traversal, including Windows separators and absolute paths. Non-WAV requests and
  missing memos/clips return 404.
- Uploads use sanitized filename stems and UTC microsecond timestamps, save audio
  under `data/uploads/`, and invoke the unchanged pipeline directly with an empty
  per-memo mock parser. Success returns 201 and the memo ID. Unsupported extensions
  return 400; pipeline failures return 500 JSON with a clear detail message.
  Failed sources and partial artifacts remain inspectable, but incomplete song
  maps are hidden from the picker. Existing output folders are never overwritten.
- Built one plain HTML/CSS/JS page with system light/dark colors, memo picker,
  upload progress/errors, an SVG activity timeline with selectable take brackets,
  section columns/cards, and an ordered listening queue. Below 700 px the map uses
  one column; the timeline can scroll horizontally. Keyboard selection is supported.
  Web Audio decodes all clips before scheduling, starts each source at the previous
  start plus decoded buffer duration, highlights the active card/bracket using the
  audio clock, and stops scheduled or still-loading playback. Switching memos clears
  playback and selection. Missing clip paths disable take playback and selection.
- Seeded `outputs/fixture_demo/` using the numeric fixture: chorus A, B, and C,
  duration 28.28 seconds, complete pipeline artifacts, and clearly named quiet
  placeholder WAVs at exact take durations to sample precision. Placeholders are
  mono 16 kHz PCM-16 tones with short fades, and the generator/UI clearly identify
  them. `--audio data/test.wav` exports actual samples at the fixture boundaries
  without rerunning classification; this branch was tested using synthetic audio.
  Generated files and audio remain ignored by Git.
- Documented all additional API, storage, upload, scheduling, selection, layout,
  placeholder, and test-harness choices in the README's Local web UI section.
  API tests use direct ASGI requests instead of adding an HTTP test dependency.
  On Windows the async runtime's private wakeup sockets initialize before per-test
  network blocking; no API test opens a network connection. Upload coverage replaces
  only AST scoring and runs the actual pipeline and WAV exporter.
- Validation: focused API/demo suite **15 passed**; full offline command
  `.\.venv\Scripts\python.exe -m pytest` **92 passed** in 8.68 seconds, with the
  three existing `audioread` deprecation warnings. A final API rerun after guarding
  upload collision cleanup passed **13 tests**. Inline JavaScript passed
  `node --check`; `git diff --check` passed. The initial focused run identified and
  fixed Windows async-runtime setup under the socket guard and the required sample
  rate argument in real-audio demo loading.
- Ran `.\.venv\Scripts\python.exe scripts/make_demo_memo.py`, then started Uvicorn
  on loopback port 8765 for a live HTTP check. Home page, memo list, fixture detail,
  and all three placeholder WAVs returned 200. Stopped the owned server afterward.
  No browser tests or manual listening were performed; browser layout and audible
  gapless playback remain manual checks. Used the Sites skill's local-only workflow;
  no hosted Site was created or published.
- Run from the repo root:

  ```powershell
  .\.venv\Scripts\python.exe scripts/make_demo_memo.py
  .\.venv\Scripts\python.exe -m uvicorn app.server:app --reload
  ```

  Open `http://127.0.0.1:8000`. Optional real audio:
  `.\.venv\Scripts\python.exe scripts/make_demo_memo.py --audio data/test.wav`.

## 2026-10-30 - Torchaudio AST comparison stopped at version check

- Read `AGENTS.md` and the scoring, frame-label, and real-memo test code.
  `pip show torch torchaudio transformers` reported torch **2.14.1**,
  transformers **5.21.0**, and no installed torchaudio.
- Completed the requested fallback baseline using the unchanged scorer:

  ```powershell
  .\.venv\Scripts\python.exe scripts/score_windows.py data/test.wav --out outputs/scores_fallback.csv
  ```

  Saved **58 windows** to `outputs/scores_fallback.csv`. Captured console output
  in `outputs/score_fallback_run.log`; the run emitted the expected warning,
  "At least one mel filter has all zero values." Baseline CSV SHA-256:
  `B7E3133B3AF554599E31C36FD144BE2E02E0555E4BF900E169835E03BF202240`.
- Before any installation, requested a non-mutating pip dependency plan:

  ```powershell
  .\.venv\Scripts\python.exe -m pip install --dry-run --report outputs/torchaudio_install_plan.json torchaudio==2.14.1
  ```

  Pip exited 1: `No matching distribution found for torchaudio==2.14.1`.
  Available versions reported by the configured package index ended at **2.11.1**.
  Dependency resolution failed before a JSON installation plan was generated.
  The dry-run check overlapped the already-started baseline; only that existing
  baseline was allowed to finish after the version check failed.
- Followed the user's explicit step 3 STOP condition. Installed no packages and
  did not try another torchaudio version or change torch. A subsequent `pip show`
  confirmed torch remains **2.14.1** and torchaudio remains uninstalled.
  No changes to `keeper/`, `requirements.txt`, or the fixture scores were made
  for this task; earlier UI/evaluation working-tree changes remain untouched.
- Singing/speech/instrument mean, median, and maximum absolute differences are
  **not measured**, and changed frame-label count is **not measured** because
  no torchaudio scoring run was possible under the exact-version constraint.
  Neither step 6 branch was taken. No new fixture accuracy, recalls, or take
  boundaries were computed, and the full test suite was not rerun because
  the experiment stopped before any installation, code, or fixture changes.

## 2026-10-30 - Pin direct dependencies for reproducibility

- Read `AGENTS.md` and used `.\.venv\Scripts\python.exe -m pip freeze` to read
  the exact installed versions. Pinned only the 13 existing direct dependencies
  in `requirements.txt`: torch 2.14.1, transformers 5.21.0, librosa 0.11.0,
  soundfile 0.14.0, numpy 2.4.6, pandas 3.1.0, scipy 1.17.1, pytest 9.2.0,
  python-dotenv 1.2.4, openai 3.41.0, fastapi 0.162.0, uvicorn 0.54.0, and
  python-multipart 0.0.32. Added no transitive dependencies or new packages,
  and installed or upgraded nothing. Reading the freeze required execution
  outside the sandbox because its base Python executable is inaccessible inside.
- Added the requested one-line Known limitations section to `README.md`, stating
  that AST uses the transformers numpy filterbank fallback because no torchaudio
  release matches torch 2.14.1 and that repo results were measured on that path.
  Updated the README installation command to use `-r requirements.txt` so it
  honors the pins. No application logic, tests, or fixture data changed.
- Validation: `.\.venv\Scripts\python.exe -m pytest` passed **92 tests** in
  13.10 seconds on Python 3.11.9, with the three existing `audioread` deprecation
  warnings (`aifc`, `audioop`, `sunau`). `git diff --check` passed. Preserved all
  earlier working-tree changes.
