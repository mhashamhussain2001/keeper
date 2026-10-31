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
- Uploads call `run_pipeline` directly with `MockLabelParser` and a per-memo empty
  `mock_labels.json`, equivalent to the requested mock CLI mode. Processing uses
  the existing local AST scorer and exporter; an uncached model produces a JSON
  error. Success returns HTTP 201; unsupported extensions return 400; processing
  failures return 500 with a `detail` message. Failed uploads retain their source
  and partial artifacts for inspection, but no song map is listed as complete.
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
