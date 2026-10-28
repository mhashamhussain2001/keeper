# Keeper

Offline core for finding sung takes in a songwriting memo and organizing them by
spoken commands. There is no UI and no external service integration.

Use Python 3.11 and a virtual environment in `.venv`. The only direct requirements
are the nine packages in `requirements.txt`.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m keeper.pipeline --help
```

Run the complete pipeline without loading AST:

```powershell
.\.venv\Scripts\python.exe -m keeper.pipeline data/memo.wav --parser mock --mock-labels commands.json --scores-csv scores.csv --out outputs/memo/
```

The CSV needs `start`, `end`, `singing`, `speech`, and `instrument` columns. Times
are seconds and group scores are probabilities in `[0, 1]`. Every audio frame
must overlap a score window. Extra raw-label columns are preserved in the output.
Mock commands are JSON dictionaries keyed by speech-region start times:

```json
{
  "15.0": {
    "is_command": true,
    "section": "chorus",
    "variant": "B",
    "note": "keep that one",
    "confidence": 1.0
  }
}
```

The output directory receives PCM-16 WAV clips, `songmap.json`, `regions.json`,
`window_scores.csv`, and `warnings.txt`. Source audio is never modified. `data/`,
`outputs/`, virtual environments, credentials, and audio formats are gitignored.
Tests generate all audio and command fixtures in temporary directories.

To score real audio separately once AST is available in the local Hugging Face
cache:

```powershell
.\.venv\Scripts\python.exe scripts/score_windows.py data/memo.wav --out outputs/scores.csv
```

Both AST artifacts are loaded with `local_files_only=True`. Missing cached files
cause an error; this scaffold does not download weights or contact a service.
The CSV path and either CLI's help do not import Torch or Transformers.

Implementation choices where the specification leaves details open:

- Frame smoothing uses centered, truncated neighborhoods at memo edges. All ties
  preserve the original frame label.
- Short regions are processed in chronological order, one per pass. Equal-length
  neighbors choose the left neighbor. A lone short region stays unchanged.
- `merge_frames` covers the frame grid; the pipeline trims its last region to the
  exact audio duration before absorption. Partial RMS frames use actual samples,
  without padding or diluting their RMS.
- `merge_frames` initializes `mean_scores` as an empty dictionary because its
  inputs contain no scores. The pipeline fills these with duration-weighted means
  of the overlapping frame probabilities after region smoothing.
- Parsers receive a copy of just the speech-region samples, while `Region` times
  remain absolute within the memo. Mock keys use Python's nearest-half-second
  rounding (ties to even) and accept numeric keys such as `"2"` or `"2.0"`.
- `cut_takes` returns provisional records. `build_songmap` assigns all final
  variants and IDs, including the verse's `"Take 1"` in the specified timeline.
  Additional ID suffixes resolve punctuation or Unicode slug collisions.
- Evaluation samples each frame at its midpoint, includes a partial final frame,
  uses truth rows and prediction columns in its confusion matrix, and reports
  zero for precision/recall with zero denominators. Activity intervals must tile
  matching durations; Audacity `cmd:` annotations are excluded from metrics.
- Export returns a copied song map. Fades span at most the clip length; overlapping
  fades multiply on very short clips. Output subdirectories are allowed, but
  writing directly into a source file's folder is rejected. Pipeline source and
  exported clip paths are stored as absolute paths.
