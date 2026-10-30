# Real memo numeric fixture

`window_scores.csv` is an unchanged copy of `outputs/scores.csv`; `truth_labels.txt` is an unchanged copy of `data/test_labels.txt`. Legacy singing/instrument truth labels are mapped to music by the Audacity reader. There is no audio or lyric transcription in this fixture.

`rms_db.json` contains `rms_db_per_frame(load_audio("data/test.wav", 16000), 16000, 0.5)`, rounded to one decimal place. The audio is 28.579125 seconds long and yields 58 RMS values. `meta.json` specifies the requested evaluation duration of 28.28 seconds, so the pipeline uses the first 57 values and clamps the final region to 28.28 seconds.

Before writing `mock_commands.json`, the computed speech regions were printed as 8.0–9.5, 21.0–22.5, and 27.0–28.28 seconds. Those starts key chorus A, B, and C. Commands come from the provided labels; they do not alter classifier predictions.

The default configuration yields a silence gate of -45.26 dB and 49/57 correct activity frames (85.9649%). Music and speech recall are 100%; silence recall is 10/18 (55.5556%). Takes are A 0.00–8.00, B 10.20–21.00, and C 22.70–27.00 seconds, with no unlabeled takes.
