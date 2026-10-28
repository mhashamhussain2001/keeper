# Task: scaffold the Keeper pipeline (no API calls, no UI, no real audio yet)

You are building the first version of **Keeper**, a tool that takes a long songwriting voice memo, finds sung takes, and files them into a song map using labels the songwriter speaks after each take (for example "that's Chorus Option B", "keep that one").

This session builds the deterministic core and its tests. Do NOT build the web UI, and do NOT call any external API. Real recordings arrive tomorrow, so everything must be testable with synthetic data today.

Read `AGENTS.md` first and follow it.

---

## 1. Environment

- Python 3.11, virtual environment in `.venv`.
- Create `requirements.txt` with exactly: `torch`, `transformers`, `librosa`, `soundfile`, `numpy`, `pandas`, `scipy`, `pytest`, `python-dotenv`. No others without asking me.
- `.gitignore` must contain: `.env`, `.venv/`, `__pycache__/`, `data/`, `outputs/`, `*.wav`, `*.m4a`, `*.mp3`, `*.flac`.
- Create `.env.example` with these names and empty values: `META_API_KEY=`, `BASETEN_API_KEY=`.

## 2. Folder structure (create exactly this)

```
keeper/
  __init__.py
  config.py
  types.py
  audio_io.py
  segment_ast.py
  frames.py
  regions.py
  labels.py
  cut.py
  songmap.py
  export.py
  pipeline.py
eval/
  __init__.py
  audacity_labels.py
  evaluate.py
tests/
  test_frames.py
  test_regions.py
  test_cut.py
  test_songmap.py
  test_audacity_labels.py
  test_pipeline_mock.py
scripts/
  score_windows.py
data/            (gitignored, create empty with a .gitkeep is NOT allowed since data/ is ignored; just create the folder)
outputs/         (gitignored)
```

## 3. Conventions (apply everywhere)

- Audio is always a 1-D `numpy.float32` array, mono, at **16000 Hz**.
- All times are **seconds as float**. Never use sample indices in public function signatures.
- Every public function has type hints and a one-line docstring stating what it returns.
- No global state. All tunable numbers live in `config.py`; no magic numbers elsewhere.
- The source audio file is never modified (non-destructive).

## 4. `config.py`

One frozen dataclass `KeeperConfig` with these fields and defaults:

| field | default | meaning |
|---|---|---|
| `sample_rate` | 16000 | Hz |
| `win_s` | 2.0 | classifier window length, seconds |
| `hop_s` | 0.5 | step between windows, seconds; also the frame size |
| `silence_db` | -45.0 | frame RMS below this (dBFS) is silence |
| `voice_min` | 0.20 | if max(singing, speech) is below this, frame is instrument |
| `mode_filter_frames` | 3 | odd window size for majority-vote smoothing of frame labels |
| `min_region_s` | {"singing": 2.0, "speech": 0.5, "instrument": 1.0, "silence": 0.5} | regions shorter than this get absorbed |
| `max_gap_s` | 4.0 | longest silence/instrument gap allowed inside one take |
| `pre_roll_s` | 0.3 | audio kept before a take's first singing |
| `post_roll_s` | 0.5 | audio kept after a take's last singing |
| `fade_ms` | 10.0 | fade in/out on exported clips to avoid clicks |

## 5. `types.py`

Dataclasses (use `@dataclass`, all JSON-serializable through a `to_dict()` method):

- `FrameLabel = Literal["singing", "speech", "instrument", "silence"]`
- `Region(start: float, end: float, kind: FrameLabel, mean_scores: dict[str, float])`
- `CommandLabel(is_command: bool, section: Literal["intro","verse","pre-chorus","chorus","bridge","hook","outro","unsorted"], variant: str | None, note: str | None, confidence: float)`
- `Take(id: str, section: str, variant: str, start: float, end: float, source_file: str, note: str | None, clip_path: str | None)`
- `SongMap(source_file: str, sections: dict[str, list[Take]])` with `to_json(path)` and `from_json(path)`.

## 6. `audio_io.py`

- `load_audio(path, sr) -> np.ndarray`: load any format librosa supports, convert to mono float32 at `sr`.
- `rms_db_per_frame(y, sr, hop_s) -> np.ndarray`: RMS in dBFS for consecutive non-overlapping frames of length `hop_s`. Use `20*log10(rms + 1e-10)`. Length = `ceil(len(y) / (hop_s*sr))`.

## 7. `segment_ast.py`

- Model: `MIT/ast-finetuned-audioset-10-10-0.4593` via `ASTFeatureExtractor` and `ASTForAudioClassification`.
- `class ASTScorer` with `score_windows(y, sr, win_s, hop_s) -> pd.DataFrame`.
  - Window i covers `[i*hop_s, i*hop_s + win_s)`. Last window may be zero-padded.
  - Batch 8 windows per forward pass, `torch.no_grad()`.
  - Use **sigmoid** on logits (AudioSet is multi-label), never softmax.
  - Output columns: `start`, `end`, `singing`, `speech`, `instrument`, plus every raw label in the groups below.
- Class groups (score of a group = **max** over its member labels):
  - singing: `Singing`, `Male singing`, `Female singing`, `Child singing`, `Humming`, `Rapping`
  - speech: `Speech`, `Male speech, man speaking`, `Female speech, woman speaking`, `Conversation`, `Narration, monologue`
  - instrument: `Music`, `Musical instrument`, `Guitar`, `Acoustic guitar`, `Piano`, `Keyboard (musical)`
- On load, check every label name above exists in `model.config.id2label`. If any is missing, raise `ValueError` listing the missing names. Do not silently skip.
- Define a `Scorer` Protocol in this file (`score_windows` signature above) so a second scorer (Muse Spark Open) can be added later without touching downstream code.

## 8. `frames.py`: window scores to per-frame labels

- Frame k covers `[k*hop_s, (k+1)*hop_s)`.
- Frame score for each group = **mean** of that group's score over all windows that overlap the frame.
- Label each frame in this order:
  1. if frame RMS dB < `silence_db` -> `silence`
  2. else if `max(singing, speech) < voice_min` -> `instrument`
  3. else if `singing >= speech` -> `singing`
  4. else -> `speech`
- Then apply a majority-vote (mode) filter of width `mode_filter_frames`. Ties keep the original label.
- Return `frame_labels_from_scores(scores_df, rms_db, cfg) -> list[FrameLabel]`.

## 9. `regions.py`

- `merge_frames(labels, hop_s) -> list[Region]`: consecutive equal labels become one region.
- `absorb_short(regions, cfg) -> list[Region]`: repeatedly, any region shorter than `min_region_s[kind]` is relabeled to the kind of its **longer neighbor** (if only one neighbor, that one), then re-merge. Loop until no change. Must terminate.
- Regions must tile the whole memo: no gaps, no overlaps, first starts at 0, last ends at memo duration.

## 10. `labels.py`

- `class LabelParser(Protocol)`: `parse(audio: np.ndarray, sr: int, region: Region) -> CommandLabel`.
- `class MockLabelParser`: built from a JSON file mapping region start time (rounded to 0.5 s, as a string key) to a `CommandLabel` dict. Any speech region not in the file returns `is_command=False`.
- `class MuseSparkLabelParser`: stub only. Constructor reads `META_API_KEY` from env; `parse` raises `NotImplementedError("Muse Spark audio input not verified yet")`. Include a docstring describing the intended prompt: send the speech region audio, ask for JSON with fields of `CommandLabel`.
- Only regions of kind `speech` are ever sent to a parser.

## 11. `cut.py`: command -> take boundaries

For each speech region R where the parser returned `is_command=True`, in time order:

1. Walk **backward** from R through earlier regions.
2. Collect regions of kind `singing`. Allow `silence` or `instrument` regions in between **only if each is shorter than `max_gap_s`**.
3. Stop walking at the first of: a `speech` region (command or not), a silence/instrument region of length >= `max_gap_s`, the end of the previous take, or time 0.
4. If no singing region was collected, produce no take and record a warning `"command at {t}s had no preceding singing"`.
5. Take `start` = start of earliest collected singing region minus `pre_roll_s`, but never before the stopping boundary.
6. Take `end` = end of latest collected singing region plus `post_roll_s`, but never after `R.start`.

Return `list[tuple[Take, CommandLabel]]` plus `list[str]` warnings.

## 12. `songmap.py`

- `build_songmap(takes_with_labels, source_file) -> SongMap`.
- Section key = `CommandLabel.section`. Commands like "keep that one" with no section go to `unsorted`.
- Variant: use the spoken variant if present (e.g. "B"). If None, assign `"Take N"` where N counts takes already in that section, starting at 1.
- If the same section+variant appears twice, keep both; suffix the later one `" (2)"`, `" (3)"`.
- Take ids: `"{section}-{variant}"` slugified.

## 13. `export.py`

- `export_clips(y, sr, songmap, out_dir, fade_ms) -> SongMap`: write each take as a 16-bit WAV with linear fade in/out of `fade_ms`, fill `clip_path`, return the updated SongMap. Never write to the source file's folder.

## 14. `pipeline.py` (CLI)

```
python -m keeper.pipeline <audio_path> --parser mock --mock-labels <json> --out outputs/<name>/
```
Steps: load -> ASTScorer -> rms -> frames -> regions -> parse speech regions -> cut -> songmap -> export -> write `songmap.json`, `regions.json`, `window_scores.csv`, `warnings.txt` into the out dir. Print a short summary: number of regions by kind, number of takes by section, warnings.

Add a flag `--scores-csv <path>` that skips AST and loads window scores from CSV, so the downstream logic can be run without the model.

## 15. `eval/audacity_labels.py` and `eval/evaluate.py`

- Audacity label export format: one line per label, tab-separated `start\tend\tlabel`. Point labels have start == end.
- `read_audacity_labels(path) -> list[tuple[float, float, str]]`.
- Ground-truth convention: region labels are `singing`, `speech`, `instrument`, `silence`. Command labels are point or span labels starting with `cmd:` followed by JSON-free text like `cmd:chorus B` (just store the raw text for now).
- `evaluate.py`: `frame_accuracy(pred_regions, gt_regions, hop_s) -> dict` returning overall accuracy, per-class precision/recall, and a 4x4 confusion matrix as a nested dict. Compare at frame resolution `hop_s`.

## 16. `scripts/score_windows.py`

Standalone: `python scripts/score_windows.py <audio> --out outputs/scores.csv` runs only ASTScorer and saves the window table. Used for tomorrow's first real-audio test.

## 17. Tests (pytest, must all pass, no network, no model download)

Use **synthetic window-score DataFrames and synthetic RMS arrays**, never the AST model, in tests.

- `test_frames.py`: silence override wins over high voice scores; low voice -> instrument; singing vs speech tie goes to singing; mode filter removes a single-frame blip.
- `test_regions.py`: regions tile the memo exactly; a 0.5 s singing blip inside speech is absorbed; absorb loop terminates on alternating short regions.
- `test_cut.py`: build this timeline by hand and assert exact take boundaries:
  - 0-3 silence, 3-15 singing, 15-17 speech (command: chorus A), 17-20 instrument, 20-32 singing, 32-33 silence, 33-40 singing, 40-42 speech (command: chorus B), 42-60 singing, 60-61.5 speech (not a command), 61.5-70 singing, 70-72 speech (command: verse, variant None).
  - Rule for the stopping boundary: it is the **end** of the region that stopped the walk (or 0.0 if the walk reached the start of the memo). Take start is clamped so it is never earlier than this boundary.
  - Expected takes (assert exactly):
    - chorus A: start 2.7, end 15.0. Walk collects 3-15 singing, passes the 0-3 silence (3 s < 4 s), reaches time 0, boundary 0.0. Start 3.0 - 0.3 = 2.7. End 15.0 + 0.5 = 15.5, clamped to command start 15.0.
    - chorus B: start 19.7, end 40.0. Walk collects 33-40 and 20-32 singing, passes the 32-33 silence (1 s) and the 17-20 instrument (3 s), stops at the 15-17 speech, boundary 17.0. Start 20.0 - 0.3 = 19.7 (after 17.0, so kept). End clamped to 40.0.
    - verse: section "verse", variant "Take 1", start 61.5, end 70.0. Walk collects 61.5-70 singing, stops at the 60-61.5 non-command speech, boundary 61.5. Start 61.5 - 0.3 = 61.2, clamped to 61.5.
    - The 42-60 singing has no command after it before other speech, so it produces no take.
  - Also: a command with no preceding singing yields zero takes and one warning.
- `test_songmap.py`: unsorted handling, auto "Take N" numbering, duplicate variant suffixing, JSON round-trip equality.
- `test_audacity_labels.py`: parses a small inline label file including a point label.
- `test_pipeline_mock.py`: run the pipeline end to end with `--scores-csv` on a synthetic 30 s sine/noise WAV generated inside the test into a temp dir, plus a mock labels JSON; assert songmap.json exists and has the expected sections.

## 18. Definition of done

- `pytest` passes with zero network access.
- `python -m keeper.pipeline --help` works.
- No file in the repo contains an API key or audio.
- At the end, reply with: (a) a tree of created files, (b) for each module, 2 bullets on what it does, (c) any place where you had to make a decision this spec did not cover, listed explicitly so I can review it.

Do not start on: FastAPI, frontend, Muse Spark API calls, Baseten, Modal.
