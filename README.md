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
