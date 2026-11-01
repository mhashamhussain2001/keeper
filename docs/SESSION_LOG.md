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

## 2026-10-31 - Memo 2 oracle evaluation

- Read `AGENTS.md`. The local second memo was named `data/test_2.wav` with
  `data/test_2_labels.txt`; copied these to the requested `data/memo2.wav` and
  `data/memo2_labels.txt`, preserving the originals. Fixed line 2 from ISNT to
  INST, then appended the nine provided command truth intervals and five take
  truth intervals before evaluation. All data and generated audio stay ignored.
- Added case-insensitive SING, INST, SPEECH, and UNCLEAR aliases in the Audacity
  reader. Singing/instrument aliases retain the existing collapse to music for
  three-class evaluation. Added `parse_command_truth`, parsing file/keep/reject/
  none, optional section/target, and notes consuming the rest of the line.
- Added `scripts/eval_memo.py <name>`. It reuses an existing score CSV without
  loading AST, or generates and caches scores once if absent. A no-export pass
  through the unchanged pipeline supplies predicted speech regions. Oracle
  commands attach by greatest positive overlap; absent overlap is a missed
  command. Collisions retain the latest chronological truth command and audit
  superseded commands. A second cached pass exports clips with oracle labels.
  file uses its section (unsorted if absent); keep uses unsorted; reject/none
  map to noncommands. target=next retains previous-take behavior and is reported
  as known unsupported. Pipeline logic and thresholds are unchanged.
- Extended frame evaluation with optional midpoint-based exclusions. Memo
  evaluation excludes UNCLEAR intervals from three-class accuracy/precision/
  recall and counts their predicted classes separately. All gaps count as
  silence. Primary positive take hits require the matching section and at least
  50% truth-duration coverage; rejected takes are checked separately for any
  surviving overlap, including unlabeled output. Every unlabeled take is an
  unscored extra. Existing section-agnostic boundary metrics are secondary.
- Added offline tests for aliases, grammar, exclusions, oracle assignment,
  collisions, misses, rejected/unlabeled outputs, 50% section matching, actual
  clip export, and cache generation/reuse. Full suite: **121 passed** in 25.98 s,
  with the three existing audioread deprecation warnings. After clarifying the
  optional-argument grammar, the focused suites passed **50 tests** in 21.31 s.
- No `outputs/memo2_scores.csv` existed initially. Started its one-time generation
  with the existing `scripts/score_windows.py`, cached AST model, and unchanged
  numpy fallback. Set OMP_NUM_THREADS and MKL_NUM_THREADS to 4 for this scoring
  process only; no model, classification, or cutting settings were changed.
- Completed fresh scoring: **753 windows**, approximately **47 min 46 s** wall
  time (observed process start 01:20:47 to cache completion 02:08:33 local time).
  The expected all-zero mel-filter warning appeared. Cache SHA-256:
  `EFFC91F24B4A8BF53265F8720FF53200A883F868E7A27D9BADBA4AD68B546D77`.
- Ran `.\.venv\Scripts\python.exe scripts/eval_memo.py memo2` successfully.
  It reused the completed cache without rescoring; cached evaluation took
  **8.07 s** for the two passes. Memo duration **376.4426875 s**. Exported
  **10 nonempty WAV clips**: verse 1, chorus 2, unsorted 1, bridge 1, unlabeled 5.
  Saved `outputs/memo2_eval/report.md`, song map, regions, scores, oracle labels,
  attachment audit, warnings, and clips. These generated outputs remain ignored.
- Primary frame accuracy: **622/680 = 0.914706**. Excluded **73 UNCLEAR frames**,
  all predicted music. Class recall / precision: music **0.998012 / 0.901257**;
  speech **0.793103 / 0.884615**; silence **0.655405 / 1.000000**. Confusion
  matrix in music/speech/silence order (truth rows, prediction columns):
  `[502, 1, 0]`, `[6, 23, 0]`, `[49, 2, 97]`.
- Oracle command detection: **7/9** commands overlapped predicted speech,
  **7 applied**, **2 missed**, **0 superseded**. Misses: cmd:reject at
  97.606333-97.860293 s, and cmd:file section=chorus note=that last one at
  120.669939-122.586183 s. Both bridge target=next commands attached but remain
  known unsupported label-before-take cases. Pipeline warned that the command
  at 288.5 s had no preceding music.
- Primary positive take hits: **2/4**. Verse 21.95-37.74 s and chorus
  348.67-372.73 s hit with 100% truth coverage in the matching section.
  Chorus 114.87-120.11 s and bridge 294.84-305.10 s missed. The rejected truth
  take 92.32-97.31 s was still produced as unlabeled-take-1 with 100% coverage.
- Unlabeled extras (unscored): take 1 **91.7-98.5 s**, take 2 **103.7-123.0 s**,
  take 3 **130.2-150.5 s**, take 4 **155.7-198.0 s**, take 5 **290.7-306.0 s**.
- Secondary section-agnostic boundary metrics: **5 matches, 0 misses, 5 extras**.
  Absolute start/end errors, in truth order: verse **20.25/1.26 s**;
  rejected **0.62/1.19 s**; first chorus **11.17/2.89 s**;
  bridge **4.14/0.90 s**; final chorus **38.67/0.77 s**.
- `git diff --check` passed and `git diff -- keeper/` is empty. Added no
  dependencies, changed no pipeline logic or thresholds, and performed no
  tuning based on these results. The complete generated report is pasted
  in the task reply and retained in `outputs/memo2_eval/report.md`.

## 2026-10-31 - Memo2 diagnostics (report only)

- Read `AGENTS.md` and `private/codex_prompt_memo2_diagnostics.md`. Added
  `scripts/diagnose_memo.py` and `tests/test_diagnose_memo.py`; generated
  `outputs/memo2_eval/diagnostics.md` and `diagnostics_frames.csv`. Full-memo
  scores, truth, clips, song map, pipeline files, and thresholds are unchanged.
  No dependencies added; ROC calculations use the already-listed SciPy package.
  Verified the full-memo score SHA-256 remains
  `EFFC91F24B4A8BF53265F8720FF53200A883F868E7A27D9BADBA4AD68B546D77`.
- Reconstructed raw gating, island voting, mode filtering, and absorb_short;
  checked the trace against `keeper.frames.frame_labels_from_scores` and the
  existing final regions. Both missed commands were erased by island voting:
  the reject command's 6 s island (92-98 s) had music in 11/12 raw frames;
  the chorus command's 8 s island (114.5-122.5 s) had music in 11/16 frames.
  The speech scores exceeded 0.40; the inclusive <=8 s voting rule applied.
- Singing/instrument midpoint truth counts: 396/107. Higher-means-singing ROC
  AUC: singing group 0.986501; Singing 0.985840; Humming 0.968871; instrument
  group 0.655079; Music 0.665652; singing-minus-instrument 0.446686. The best
  score was singing group, with diagnostic threshold >=0.0018018119735643268
  maximizing in-sample balanced accuracy 0.944810 (singing recall 0.992424,
  instrument recall 0.897196). This threshold was not applied to the pipeline.
- Simulated edge-only trimming, preserving internal frames and bounded 0.3 s
  pre-roll / 0.5 s post-roll. Current -> simulated absolute start/end errors:
  verse 20.250/1.260 -> 1.250/0.760 s; rejected 0.620/1.190 unchanged;
  early chorus 11.170/2.890 -> 11.170/1.890 s; bridge 4.140/0.900 ->
  0.360/0.900 s; final chorus 38.670/0.770 unchanged. Retained 9/10 simulated
  takes (dropped bridge-take-1); truth misses stayed zero. No clips were edited.
- Memo1 fixture has 32 singing frames and no instrument truth, so its AUC is
  undefined. Singing-group median 0.103803; 0/32 singing frames (0%) would be
  instrument-classified by the memo2 rule. This preserves observed fixture
  singing but does not establish general safety or instrument specificity.
- Located all 49 truth-silence/predicted-music frames: 40 were already above
  the RMS gate (-40.764085 dB); 9 raw-silence frames were filled by the mode
  filter; 0 were absorbed by absorb_short. Listed every contiguous run in the
  report. RMS covers whole frames while truth uses midpoints, so boundary
  frames may contain music even when scored as silence; no claim that RMS
  alone identifies room noise versus piano sustain.
- Benchmarked only the first 60 seconds (120 windows), loading the local AST
  model once; kept prefix benchmark CSVs and a resumable checkpoint separate
  from the full memo cache. Runtime: torch 2.14.1+cpu, initial threads 4,
  os.cpu_count() 8; 11th Gen Intel Core i7-1165G7 @ 2.80GHz. Model load
  23.909 s excluded from scoring timings. Single sequential trials:
  - batch 8 / threads 4: 266.701 s, 2.222507 s/window, max score diff 0;
  - batch 16 / threads 4: 302.396 s, 2.519968 s/window, diff 1.93715096e-7;
  - batch 32 / threads 4: 311.621 s, 2.596841 s/window, diff 1.93715096e-7;
  - batch 8 / threads 8: 338.851 s, 2.823760 s/window, diff 0.
  All differences were <1e-5 across all exposed raw/group scores. Current
  batch 8 / four threads was fastest: 4.445 processing minutes per audio
  minute, with no speedup from the tested changes. Temporary runtime overrides
  were restored; no defaults changed. Some diagnostics/tests overlapped timings,
  so small differences are noisy. Prefix timing does not fully explain the old
  47-minute full run; additional profiling would be required for attribution.
- Report ends with proposed fixes, conditional metric effects, and memo1 risks;
  implemented none of those fixes. Ran the final CLI using completed benchmark
  checkpoints (no repeat inference), and retained the full report for the reply.
- Validation: diagnostic suite **11 passed**; final full command
  `.\.venv\Scripts\python.exe -m pytest` **132 passed** in 10.37 s, with the
  three existing audioread deprecation warnings. `git diff --check` passed;
  `git diff -- keeper/` is empty. Full report command:
  `.\.venv\Scripts\python.exe scripts/diagnose_memo.py memo2`.

## 2026-10-31 - Protected speech and edge trim with cached ablation

- Read `AGENTS.md`. Added configurable raw-speech-run protection to island
  voting: runs >=`protect_speech_run_s=1.0` retain speech when enabled through
  `protect_speech_runs`. Other minority frames retain the existing voting
  behavior. Added tests for the 2.5 s run in an 8 s island, isolated 0.5 s
  speech, inclusive 1 s protection, and separated short runs.
- Added `keeper/trim.py`, applied after commanded and unlabeled candidates
  are collected and before naming/export. With `edge_trim=True`, the first
  and last overlapping frame reaching `trim_singing_min=0.0018` define the
  core; reapply existing pre/post roll within the original take. Keep internal
  frames and metadata unchanged. Drop takes without threshold-level singing
  and write `take dropped: no singing detected (<start>-<end>)`. No section
  or command-status exceptions, including surviving rejected/unlabeled takes.
  Added unit and pipeline coverage for trim ends, internal frames, rolls,
  inclusive threshold, disabled mode, all sections, drops, and warning output.
- Added `scripts/ablate_memos.py`, which requires existing caches and blocks
  AST construction explicitly. Regenerated oracle speech attachments for each
  setting, with no clip exports or threshold tuning. Memo1 uses numeric fixture
  RMS, legacy command truth normalized with A/B/C variants, and the three
  existing test_real_memo expected take spans (the fixture has no take: rows).
  UNCLEAR is excluded; positive hits require matching section and >=50% truth
  coverage. Rejected truth is excluded from positive hits but included in
  section-agnostic boundary matching. Saved both tables and detailed JSON to
  `outputs/{memo1,memo2}_eval/ablation.*` plus `outputs/ablation_report.md`.
- Memo2 results (none / protect only / trim only / both):
  - Frame accuracy: **0.914706 / 0.920588 / 0.914706 / 0.920588**.
  - Music recall: **0.998012** in all cases; speech recall:
    **0.793103 / 0.931034 / 0.793103 / 0.931034**; silence recall **0.655405**.
  - Commands detected: **7/9 / 8/9 / 7/9 / 8/9**; positive take hits:
    **2/4 / 3/4 / 2/4 / 3/4**.
  - Verse S/E errors: **20.250/1.260**, **20.250/1.260**,
    **1.250/0.760**, **1.250/0.760** seconds.
  - Rejected take: **0.620/1.190** throughout.
  - Early chorus: **11.170/2.890**, **10.870/0.110**, **11.170/1.890**,
    **10.870/0.110** seconds.
  - Bridge: **4.140/0.900**, **4.140/0.900**, **0.360/0.900**,
    **0.360/0.900** seconds. Final chorus: **38.670/0.770** throughout.
  - Trim cases drop the instrument-only candidate **279.5-285.0 s** with
    the specified warning. The existing unsupported-next warning at 288.5 s
    remains. Target-next and rejection semantics were not changed.
- Memo1 results: none and trim-only are identical: accuracy **0.859649**,
  recalls music/speech/silence **1.0/1.0/0.555556**, command detection **3/3**,
  positive hits **3/3**, and A/B/C S/E errors **0/0**, **0.320/0.360**,
  **0.350/0.150** seconds. Protect-only and both lower accuracy to **0.824561**,
  recalls to **0.968750/1.0/0.500000**, and worsen B end error to **1.140 s**;
  oracle detection/hits remain 3/3. A/C boundaries are unchanged.
- Default decision: **protect_speech_runs=False, edge_trim=True**. The initial
  both-on check failed the unchanged real-memo test at its existing >=0.85
  accuracy assertion; the original fixed mock speech keys also lost take B
  after protection shifted speech boundaries. Oracle ablation isolated the
  regression to protection, while trimming alone preserved every memo1 metric
  and boundary. No test_real_memo assertion or expected boundary was edited.
  Fixed thresholds remain **1.0 s** and **0.0018**, with no tuning.
- Updated README Known limitations with short-command risk, in-sample trim
  provenance (memo2 AUC 0.987, checked only on memo1 singing), flag defaults,
  and the reproducible ablation command. Updated the older diagnostic replay
  to explicitly disable both fixes when verifying saved pre-fix regions.
- Input score SHA-256 values unchanged: memo2
  `EFFC91F24B4A8BF53265F8720FF53200A883F868E7A27D9BADBA4AD68B546D77`;
  memo1 `8A299EB2948A8BCA5651CC1BF90F27FDED8DA1D340F2213F279FD531A7703A96`.
  No AST rescoring, dependency additions, or fixture data edits.
- Validation: focused suites **27 passed**; full
  `.\.venv\Scripts\python.exe -m pytest` **152 passed** in 8.08 s, with
  the three existing audioread deprecation warnings. The unchanged
  `test_real_memo.py` passes under the chosen defaults. `git diff --check`
  passed. Preserved the earlier uncommitted diagnostics work and session log.

## 2026-10-31 - Baseten Muse Spark parser, intent handling, and cached evaluation

- Read `AGENTS.md`; prerequisite satisfied by existing commit `1a5134b`
  (diagnostics and protected-speech/trim ablation). Implemented real
  `MuseSparkLabelParser` using Baseten, model
  `meta-models/muse-spark-open-1026`, reasoning effort low, max_tokens 2000,
  temperature 0. The installed `openai==3.41.0` was already pinned in
  requirements.txt, so no dependency install or requirements edit was needed.
  SDK automatic retries are disabled; the parser owns retries and accounting.
- Module prompt includes file/keep/reject/none/performance/noise. Added strict
  six-field JSON validation, wrapped-object extraction, one retry on bad
  JSON/enums, truncation, empty content, or audio refusal, and final none/0
  fallback. Compatibility fallbacks move reasoning effort to extra_body or
  omit unsupported temperature. Cache retains every completed attempt's raw
  response, decoded JSON, finish reason, reasoning/total tokens, latency,
  retry index, and failure reason. Original start/end, padded WAV SHA-1, and
  model/prompt digest form the key; results are written atomically.
- Added config `max_spark_calls=40`, preflight budget checks, and per-attempt
  enforcement. Prior cached attempts count toward the per-memo budget across
  process restarts; off/on evaluation shares one parser and cache. Client and
  dotenv/key loading are lazy, so a fully cached run needs no credentials.
  All client invocations, including rejected compatibility arguments, count
  conservatively against the guard. API authentication errors propagate;
  other call/validation failures retry once and then cache none/0.
- Speech-only clips include 0.3 s on either side, clamped to the memo, encoded
  mono 16 kHz PCM-16 WAV in an audio_url data URL. All original speech regions
  are parsed once before any cutting. Performance relabels to music, noise to
  silence, adjacent regions merge, then existing cutting/trim/export runs.
  CommandLabel retains heard/target/intent; reject creates a rejected take,
  keep uses unsorted, and file retains spoken variant detail only in note.
  target=next still attaches to previous and emits the requested warning.
- Added `--parser spark` to the pipeline and `--parser {oracle,spark}` to
  eval_memo (oracle stays default). New `scripts/spark_eval.py` handles both
  memos with protection off/on and edge trim on, oracle comparisons, clip
  exports, command transcripts/matches, intent/section accuracy, API usage,
  precision/recall/confusion, unclear frames, rejected-output checks,
  unscored unlabeled extras, and secondary boundary matches/misses/extras.
  AST construction is explicitly blocked; missing score caches raise.
- Decisions beyond the specification: require exactly the six response
  fields and a non-null section for file; reject boolean/nonfinite confidence.
  Use a 120 s SDK timeout, disable hidden SDK retries, and cache final
  failures until audio/regions/prompt change or the cache is removed.
  Cache is intended for sequential runs, without concurrent-writer locking.
  Preserve the existing oracle mappings (including reject as non-command)
  as the comparison baseline. End-to-end intent and file-section accuracy
  include undetected truth commands as incorrect; extra speech contributes
  truth none/noise. No API call is made for a command without detected speech.
- Memo1 uses real data/test.wav cropped from 28.579125 s to the fixture's
  declared 28.28 s, with fixture scores/truth/mock commands. Source audio is
  unchanged. Oracle variants A/B/C come from mock_commands.json; Spark takes
  use automatic Take N names. For mixed speech regions, command truth takes
  priority over the unmatched-chair-noise exception. Protection merges chair
  noise and chorus B into 19.5-22.5 s; Spark correctly calls the combined clip
  file/chorus, so the real run does not independently test isolated chair-noise
  classification. The none/noise exception is covered by an offline test.
- UI edits are limited to the two explicitly requested behaviors: display
  unsorted as Ideas (data key unchanged) and initially collapse rejected.
  No other UI, Modal, activity threshold, trim threshold, or fixture changes.
- Real first-run accounting (shared cache within each memo):

  | Memo / protection | New calls | Cached regions | New total tokens | Mean new-call latency | Intent accuracy | File-section accuracy |
  | --- | --- | --- | --- | --- | --- | --- |
  | memo1 off | 3 | 0 | 2575 | 3.390 s | 3/3 | 3/3 |
  | memo1 on | 1 | 2 | 963 | 3.251 s | 3/3 | 3/3 |
  | memo2 off | 7 | 0 | 6503 | 3.933 s | 7/9 | 5/6 |
  | memo2 on | 1 | 7 | 838 | 3.303 s | 8/9 | 6/6 |

  Total **12 new calls, 10,879 tokens, zero refusals, zero retries**.
  Reasoning effort low and temperature 0 were accepted directly. Memo2's
  short reject command has no speech overlap in either setting; its rejected
  performance survives as unlabeled 91.7-98.5 s. Protection recovers the
  120.67-122.59 s file command. Both next-target bridge commands warn; the
  second has no preceding music. Instrument-only 279.5-285.0 s is dropped.
- Ablation, edge trim on; S/E cells are absolute seconds, in truth-take order:

  | memo1 setting | Accuracy | Recall music/speech/silence | Commands | Positive hits | A S/E | B S/E | C S/E |
  | --- | --- | --- | --- | --- | --- | --- | --- |
  | oracle off | .859649 | 1/1/.555556 | 3/3 | 3/3 | .000/.000 | .320/.360 | .350/.150 |
  | Spark off | .859649 | 1/1/.555556 | 3/3 | 3/3 | .000/.000 | .320/.360 | .350/.150 |
  | oracle on | .824561 | .968750/1/.500000 | 3/3 | 3/3 | .000/.000 | .320/1.140 | .350/.150 |
  | Spark on | .824561 | .968750/1/.500000 | 3/3 | 3/3 | .000/.000 | .320/1.140 | .350/.150 |

  | memo2 setting | Accuracy | Recall music/speech/silence | Commands | Positive hits | Verse S/E | Rejected S/E | Early chorus S/E | Bridge S/E | Final chorus S/E |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | oracle off | .914706 | .998012/.793103/.655405 | 7/9 | 2/4 | 1.250/.760 | .620/1.190 | 11.170/1.890 | .360/.900 | 38.670/.770 |
  | Spark off | .914706 | .998012/.793103/.655405 | 7/9 | 2/4 | 1.250/.760 | .620/1.190 | 11.170/1.890 | .360/.900 | 38.670/.770 |
  | oracle on | .920588 | .998012/.931034/.655405 | 8/9 | 3/4 | 1.250/.760 | .620/1.190 | 10.870/.110 | .360/.900 | 38.670/.770 |
  | Spark on | .920588 | .998012/.931034/.655405 | 8/9 | 3/4 | 1.250/.760 | .620/1.190 | 10.870/.110 | .360/.900 | 38.670/.770 |

- Default decision: leave **protect_speech_runs=False, edge_trim=True**.
  Protection plus Spark still lowers memo1 accuracy .859649 -> .824561 and
  worsens B's end error .360 -> 1.140 s; A/C unchanged. Existing
  test_real_memo thresholds and expected boundaries are untouched and pass.
- Commands run: `.\.venv\Scripts\python.exe scripts/eval_memo.py memo2
  --parser spark`, then memo1, each repeated. Second runs made **zero new
  API calls / zero tokens**; memo2 reused 15 region accesses, memo1 six.
  Final report-only reruns also made zero calls. Complete current reports:
  `outputs/memo{1,2}/spark_report.md`; first-run token/latency reports and
  JSON preserved as `spark_report_first.md` / `spark_results_first.json`.
  Spark caches and audio exports remain ignored by Git.
- Input score SHA-256 values unchanged: memo2
  `EFFC91F24B4A8BF53265F8720FF53200A883F868E7A27D9BADBA4AD68B546D77`;
  memo1 `8A299EB2948A8BCA5651CC1BF90F27FDED8DA1D340F2213F279FD531A7703A96`.
- Validation: `.\.venv\Scripts\python.exe -m pytest`: **170 passed** in
  7.37 s, with the three existing audioread deprecation warnings. Includes
  mocked JSON/retry/refusal/truncation/fallback tests, both client argument
  fallbacks, persistent cache/budget/WAV checks, all six mappings, two-pass
  performance/noise relabels, reject cutting, padding, next warning, and
  unmatched none/noise truth. `git diff --check` passed.
