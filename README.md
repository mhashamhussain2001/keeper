# Keeper
Finds musical takes buried in long songwriting voice memos and files them into a song map, using the labels the writer speaks after each take. Activity labels are music, speech, and silence; unclaimed music goes into the separate unlabeled section.
Built for the Meta Global AI Developer Hackathon (Oct 26 to Nov 4, 2026). Work in progress.

Run the offline tests (including the real memo's numeric fixture):

```powershell
.\.venv\Scripts\python.exe -m pytest -s
```

Run the fixture without audio, model loading, or clip export:

```powershell
.\.venv\Scripts\python.exe -m keeper.pipeline tests/fixtures/test_memo --scores-csv tests/fixtures/test_memo/window_scores.csv --rms-json tests/fixtures/test_memo/rms_db.json --no-export --parser mock --mock-labels tests/fixtures/test_memo/mock_commands.json --out outputs/three_class_fixture
```

`--rms-json` requires cached scores, the mock parser, and `--no-export`. Duration comes from the fixture directory's `meta.json` (or the RMS file's neighboring `meta.json` for a file source); `--duration-s` overrides it. RMS values must cover that duration; later frames are ignored. The mock parser receives empty float32 audio and uses speech-region timestamps.

`regions.json` contains `{"silence_gate_db": ..., "regions": [...]}`. Window and region scores retain singing, speech, and instrument probabilities and add music, computed as the maximum of singing and instrument per window before averaging. Activity classification uses speech scores and RMS only.

## Known limitations

AST filterbank features use the transformers numpy fallback because no torchaudio release matches torch 2.14.1; results in this repo are measured on that path.

Commands shorter than about 0.5 s may be missed.

`trim_singing_min=0.0018` was fit in-sample on memo2 (singing-group AUC 0.987) and checked only on memo1 singing frames.

`KeeperConfig(edge_trim=True)` trims take edges by default. Speech-run protection
is available with `protect_speech_runs=True`, using `protect_speech_run_s=1.0`.
It defaults off because the cached memo1 ablation lowered frame accuracy from
0.859649 to 0.824561 and worsened the second take's end boundary. Set both
`protect_speech_runs=False` and `edge_trim=False` to reproduce the pre-fix behavior.

Run the cached-score, oracle-command ablation without loading AST:

```powershell
.\.venv\Scripts\python.exe scripts/ablate_memos.py
```

## Spark command parsing

`openai==3.41.0` (already pinned) supplies the Baseten-compatible client; no
additional dependency is needed. Set `BASETEN_API_KEY` in your local `.env`.
Spark sends only speech regions with 0.3 seconds of surrounding audio to
`meta-models/muse-spark-open-1026`. The cache and attempt audit live at
`outputs/<memo>/spark_cache.json`; the cumulative budget is 40 uncached calls
per memo, including retries. Cache hits require no key or network access.

```powershell
.\.venv\Scripts\python.exe -m keeper.pipeline data/memo2.wav --parser spark --scores-csv outputs/memo2_scores.csv --out outputs/memo2
.\.venv\Scripts\python.exe scripts/eval_memo.py memo2 --parser spark
.\.venv\Scripts\python.exe scripts/eval_memo.py memo1 --parser spark
```

Evaluation requires existing scores and never constructs AST. It runs oracle
and Spark with speech protection off and on, with edge trim on throughout,
and writes `outputs/<memo>/spark_report.md`, `spark_results.json`, and clips
under `protect_off/` and `protect_on/`. Memo1 uses `data/test.wav`, cropped
to the fixture's declared 28.28 seconds, and the existing numeric score cache.
Unannotated speech has truth intent `none`, except unmatched memo1 speech
overlapping 19-21 seconds, which has truth `noise`. A merged region containing
both chair noise and a truth command is scored against that command.

`performance` speech becomes music and `noise` becomes silence before cutting;
`reject` creates a take in `rejected`. Unsorted takes display as Ideas and the
rejected section starts collapsed. `target=next` still attaches to the previous
take and writes a warning. Spark also regressed memo1 with speech protection,
so `protect_speech_runs=False` and `edge_trim=True` remain the defaults.

## Local web UI

From the repo root, install the web dependencies, create a playable demo, and start Keeper:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/make_demo_memo.py
.\.venv\Scripts\python.exe -m uvicorn app.server:app --reload
```

Open <http://127.0.0.1:8000>. Choose a memo, play a take, or select multiple cards
or timeline brackets in listening order and click **Play selected back to back**.
The default demo has three chorus takes with clearly named placeholder tone clips.

To use the original fixture recording instead of tones:

```powershell
.\.venv\Scripts\python.exe scripts/make_demo_memo.py --audio data/test.wav
```

This uses the fixture's cached scores and take boundaries, then exports clips
from the supplied recording; it does not classify the recording again. Use the
matching original memo, long enough to contain all three takes. Audio and generated
outputs stay ignored by Git. Rerunning the command replaces the demo metadata.

Run all automated tests offline:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Implementation choices beyond the skeleton spec:

- All storage and static paths are configured in `Settings` in `app/server.py`,
  defaulting to locations relative to the repo, independent of the working directory.
  The app is local, without authentication or a database; Uvicorn defaults to loopback.
- Memo IDs are returned as a sorted JSON array. Detail returns `regions` as an
  array and takes memo duration from the greatest region end, including silence.
  Both the current object-shaped `regions.json` and older arrays are accepted.
  The picker initially chooses `fixture_demo`, otherwise the first sorted memo.
- The API converts existing absolute export paths to memo-relative paths without
  rewriting pipeline outputs. Clip URLs encode filenames and preserve subdirectories.
  Missing clip paths produce a null URL and disabled playback/selection; unavailable
  files return 404. WAV serving checks resolved containment, including symlinks,
  and rejects non-WAV files. Unknown memo IDs return 404.
- Upload IDs use an ASCII filename slug (up to 64 characters, fallback `memo`)
  and a UTC timestamp including microseconds. Upload extensions are normalized to
  lowercase and restricted to WAV, MP3, M4A, FLAC, OGG, AIF, and AIFF; the existing
  audio loader determines whether each file can actually be decoded.
- Uploads validate audio, return HTTP 201 with a memo ID, and process in a local
  background thread. With `BASETEN_API_KEY` they use Spark, otherwise an empty
  mock parser. Poll `/api/memos/<id>/status` for queued/running/done/failed and
  timestamped stages. Failed jobs retain their source and diagnostic artifacts
  but do not appear in the completed memo list. Invalid uploads return 400.
- Selection and decoded audio caches last only for the current memo. Deselecting
  and selecting again moves a take to the end; switching memos clears selection
  and stops playback. Starting another playback replaces the active queue.
  All buffers decode before scheduling, with 80 ms of initial scheduling lead;
  successive starts use the previous buffer's full decoded duration, without added
  gaps or crossfades. Stop also cancels playback still waiting for decoding.
- The SVG timeline scrolls horizontally below 640 px; take brackets use separate
  lanes when labels overlap. The song map becomes one column below 700 px.
  Unknown sections follow the requested section order. Colors follow the system
  light/dark preference. Brackets and checkboxes support keyboard selection.
- Placeholder WAVs are mono 16 kHz PCM-16, with amplitude 0.035, frequencies
  220/275/330 Hz for the three chorus takes, and 10 ms edge fades. Clip lengths
  match take duration to the nearest audio sample. The optional real-audio export
  uses the existing export fades. Old unreferenced clips are left in place when
  regenerating the demo; the new song map determines which clips the UI plays.
- API tests send in-process ASGI requests, avoiding an extra HTTP test dependency.
  Windows event-loop wakeup sockets initialize before each test's network guard;
  actual API tests remain socket-free, and upload tests stub only the AST scorer.
  Browser tests are not included; manual listening remains the playback check.

## Modal deployment

Local startup remains `.\.venv\Scripts\python.exe -m uvicorn app.server:app`.
`KEEPER_OUTPUTS_DIR` and `KEEPER_UPLOADS_DIR` optionally override local storage;
their defaults are still `outputs/` and `data/uploads/`. Uploads accept supported
audio files up to 50 MiB and ten decoded minutes. Decoding is bounded to 601
seconds during validation. The browser polls every three seconds, shows elapsed
time, opens completed memos, and displays processing errors, parser, and heard text.

Modal deployment uses the preconfigured Modal client (tested with 1.6.3) and the
existing secret named `baseten`; no credentials belong in source or build context.

```powershell
.\.venv\Scripts\python.exe -m modal deploy modal_app.py
.\.venv\Scripts\python.exe scripts/seed_modal.py https://YOUR-DEPLOYED-URL.modal.run
```

The Debian/Python 3.11 image installs the pinned requirements and ffmpeg, copies
only application packages and deployment files, and downloads AST at build time
into `/opt/huggingface`. Runtime AST loading remains `local_files_only=True`.
GPU scoring selects CUDA automatically and uses batch size 32; CPU keeps batch 8.
The optional CUDA test compares every score against CPU with absolute tolerance
1e-4 and skips when CUDA is unavailable. No pipeline thresholds or defaults change.

The `keeper-data` v2 Volume mounts at `/data`. Uploads and every status stage are
committed; web reads and GPU jobs reload before reading. One CPU web container
and one T4 worker may run, both scale to zero, with idle windows of 60/30 seconds.
GPU jobs have a 30-minute timeout. The worker receives the Baseten secret; the
web container does not. Completed legacy local demos without status.json remain
readable. Local daemon jobs do not survive a local server restart.

`scripts/seed_modal.py` uploads only `data/memo2.wav`, waits for completion,
checks `/api/memos`, and writes `outputs/modal_seed_report.json` with total,
upload/validation, queued, and processing-stage timings plus GPU/parser metadata.
It never uploads `data/uploads/` or private files. Processing status is atomic and
includes transition history for timing; there is no automatic retry of failed jobs.
