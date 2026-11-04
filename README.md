# Keeper

Memos are private from other visitors through a random token kept in each browser.
They are stored on Modal. Audio is processed on Modal GPUs, and short clips of
spoken commands are sent to Baseten for Meta's Muse Spark to read. Deleting a memo
removes its files; clearing browser data loses access to your memos. Non-showcase
memos expire after 14 days by default. Uploads default to 30 minutes and 150 MB.
Use All takes (ZIP) for recordings over 20 minutes; arrangement WAV export is
disabled for them because the browser decodes the whole original into memory.

Upload, cost and retention defaults can be changed with KEEPER_MAX_AUDIO_MINUTES,
KEEPER_MAX_UPLOAD_MB, KEEPER_OWNER_DAILY_UPLOADS, KEEPER_OWNER_DAILY_MINUTES,
KEEPER_DAILY_MINUTES, KEEPER_RETENTION_DAYS and KEEPER_UPLOADS_ENABLED. Browser
quotas and the global audio budget use a rolling 24-hour window; reservations
remain charged after deletion. The quota ledger has one web-container writer
protected by a lock. Public docs are enabled locally and disabled in production.
Run `.venv\Scripts\python.exe scripts/purge_expired.py --dry-run` to inspect a
bounded retention batch, or omit `--dry-run` to remove it; `--modal` targets the
deployed volume. Run `.venv\Scripts\python.exe scripts/check_private_strings.py`
before every push; failures reveal only file paths and line numbers.

The redesigned editor uses the supplied Keeper dark/light design tokens and
locally served Google Fonts (Figtree and Geist Mono). Dark is the default; the
theme button persists the browser's choice. Recording, take previews, and canvas
playback share the existing gesture-unlocked audio engine and iOS silent-switch
handling. The recording streams the authorized original upload; legacy memos
without that upload report that recording playback is unavailable.

Owners can file takes, and arrange stable take references on a free canvas.
Canvas sections can be added, renamed by double-click, reordered by drag, and
resized at the right edge. Drag a take onto any time/lane; drag clips to move
them, and use their remove button or Delete/Backspace to remove a selected clip.
Changes save to canvas.json. Overlapping clips play the smallest lane number;
ties use insertion order. Gaps play silence. Canvas WAV export is not implemented,
so its control is hidden. Showcase memos support playback; editing stays owner-only.

Run browser tests with the project's venv after installing the development tools:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest --browser chromium
```

Tests generate local audio, seed a local server, block external browser requests,
and write desktop dark/light and mobile screenshots under outputs/ui_screens/.
They exercise actual Web Audio playback. iPhone hardware behavior still needs a
real-device check; Chromium emulation verifies layout, not Safari audio policy.

For demo recording, set `keep_warm = 1` near the top of modal_app.py and redeploy
with `.venv\Scripts\python.exe -m modal deploy modal_app.py`. This keeps one T4
worker warm and incurs idle GPU charges. Set it back to `0` and redeploy after
the demo. Its default remains `0`.

Finds musical takes buried in long songwriting voice memos and files them into a song canvas, using the labels the writer speaks after each take. Activity labels are music, speech, and silence; unclaimed music goes into the separate unlabeled section.
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
rejected section starts collapsed. `target=next` walks forward to the next speech
or long silence, and `scope=last_attempt` keeps music after the final internal
pause of at least `attempt_gap_s=0.5`. `support_target_next=True` and
`support_last_attempt=True` enable these behaviors; disabling both reproduces
v1 cutting. A forward claim cannot be reused by a later backward command.
Spark also regressed memo1 with speech protection,
so `protect_speech_runs=False` and `edge_trim=True` remain the defaults.

Prompt v2 adds scope (default `whole`) and uses a new cache namespace. Set
`spark_prompt_v2=False` to select the old prompt. `parallel_spark=True` parses
speech in at most four threads; turning it off uses sequential parsing.
Cache keys are single-flight within a parser; writes and HTTP-call reservations
are locked. `spark_cache.calls.json` preserves the cumulative call count even
if a job stops before saving its result. Keep it alongside the result cache.

`merge_adjacent_commands=True` joins consecutive same-section commands across
silence for cutting. The joined command spans the first start to the last end
and uses `next` if any member does. Final-command metadata is retained and heard
text is combined. Activity regions and individual Spark cache entries are retained.
Set the flag false to compare with the previous cutting behavior.

`warm_librosa_on_start=True` primes librosa using an in-memory silent WAV in the
Modal GPU worker's container startup hook. The decoder is unchanged. Startup
warmup is included in cold queue time, and its duration is recorded as
`gpu_librosa_warmup_s` in the memo status. Set the flag false to disable it.

Run the v1/v2 cut ablation under both speech-protection settings:

```powershell
.\.venv\Scripts\python.exe -m scripts.ablate_logic_v2
```

Reports and clips are written to `outputs/<memo>/logic_v2/`; existing AST scores
are required and never regenerated by this evaluation. The same prompt-v2
responses are reused for v1 and v2 cutting to isolate the cut flags.

`fast_audio_loading=False` remains the default. Modal diagnostics found direct
ffmpeg faster cold, but its maximum memo1 AST difference was 0.066790, exceeding
the 0.01 acceptance limit. The optional ffmpeg path is diagnostic only; production
retains librosa. No decoder or classification thresholds were adjusted.

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
- The memo list returns display metadata with internal IDs, friendly titles,
  creation times, showcase flags, and whether the caller owns each memo. Detail returns `regions` as an
  array and takes memo duration from the greatest region end, including silence.
  Both the current object-shaped `regions.json` and older arrays are accepted.
  The picker opens the caller's newest memo, otherwise the first showcase.
- The API converts existing absolute export paths to memo-relative paths without
  rewriting pipeline outputs. Clip URLs encode filenames and preserve subdirectories.
  Missing clip paths produce a null URL and disabled playback/selection; unavailable
  files return 404. WAV serving checks resolved containment, including symlinks,
  and rejects non-WAV files. Unknown memo IDs return 404.
- Upload IDs are random 32-character UUID hex strings. Upload extensions are normalized to
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
  lanes when labels overlap. The song canvas becomes one column below 700 px.
  Unknown sections follow the requested section order. Colors follow the system
  light/dark preference. Brackets and checkboxes support keyboard selection.
- Placeholder WAVs are mono 16 kHz PCM-16, with amplitude 0.035, frequencies
  220/275/330 Hz for the three chorus takes, and 10 ms edge fades. Clip lengths
  match take duration to the nearest audio sample. The optional real-audio export
  uses the existing export fades. Old unreferenced clips are left in place when
  regenerating the demo; the new song canvas determines which clips the UI plays.
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
Completed AST scores are checkpointed before Spark, so command-service failures
retain the score cache. A failed seed also saves its stage timing report before
raising an error.

## Per-browser memo privacy and controls

This is **per-browser privacy, not accounts**. Each browser stores a random UUID
in localStorage and sends it as `X-Keeper-Owner` on every API request, including
clip fetches. Clearing browser storage or switching browsers loses access to that
browser's private memos. If storage is blocked, a warning explains that the owner
token lasts only for the page. Tokens are bearer capabilities: keep them private.
The server never includes owner tokens in memo responses and disables API caching.

`meta.json` stores title, original_filename, created_at, owner, and showcase.
The browser supplies a default title in its local time, such as
`Memo, Nov 1, 1:23 PM`; non-browser clients fall back to server UTC. Titles may
contain 1-120 characters. The picker shows titles, while IDs remain internal.
Owners can rename inline or delete with an in-page confirmation. Delete removes
both the memo folder and its matching original upload. Running/queued jobs return
409 until processing finishes, preventing a worker from recreating deleted data.
Other callers receive 404 for private detail, status, clips, rename, and delete.

Showcase memos are public read-only examples for visitors. Visibility changes
require authenticated Modal administration, not an unprotected HTTP endpoint:

```powershell
.\.venv\Scripts\python.exe scripts/set_showcase.py https://YOUR-DEPLOYED-URL.modal.run MEMO_ID on
.\.venv\Scripts\python.exe scripts/set_showcase.py https://YOUR-DEPLOYED-URL.modal.run MEMO_ID off
```

The script verifies the URL against the active Modal workspace's Keeper app.
Legacy volume memos are migrated without deletion to owner=null/showcase=false,
so they remain private unless an administrator explicitly showcases them. No
browser automatically claims legacy recordings. Migration also backfills waveforms
from saved uploads without AST or Spark. The administrative `manage_memos` function
supports `migrate`; it has no public web endpoint. Current memo2 is not a showcase.

The processing job writes 800 display-only waveform peaks to peaks.json. The
default timeline shows the quiet waveform and take blocks; Show detail reveals
activity, merging sub-1.5-second regions into the longer neighbor only for display
(ties choose the previous neighbor). Saved regions, scoring, and cutting are
unchanged. Missing legacy audio uses a flat waveform until backfill is possible.
Processed time and its expandable breakdown span the first queued timestamp
through done; they include GPU startup but exclude upload validation.

Playback uses one lazily created AudioContext, synchronously resumed and unlocked
inside a playback/seek tap before fetching or decoding. Pause/seek recreate Web
Audio sources at the saved offset; queues update the current take continuously.
Single-take fallback uses the same HTML audio element and authenticated blob URLs;
if Safari requires another gesture after fallback preparation, tap Play again.
Back-to-back playback requires Web Audio. The fixed transport respects safe-area
insets, and the iOS silent-switch hint appears once per page's first playback.
Real iPhone silent-switch/interruption behavior still requires hardware checking;
automated tests mock browser audio, not iOS hardware.

The full pytest suite also runs Node's built-in test runner (Node 18+), with no
npm packages, to verify gesture ordering, queue offsets, interruptions, fallback,
owner headers, default selection, and visual-only smoothing. Node is needed for
these frontend tests; the deployed app serves plain JavaScript without Node.
The CLI seeder sends a run-scoped owner token; use set_showcase.py explicitly
after seeding if that recording should become a public example.
