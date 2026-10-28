# Keeper: rules for the coding agent
- Python 3.11, venv in .venv. Never commit .env, audio, or data/.
- One job per module in keeper/. Don't merge responsibilities.
- Every function: type hints + one-line docstring saying what it returns.
- Audio is always 16 kHz mono float32 numpy arrays. Times are in seconds (float).
- Don't add dependencies without saying why.
- After writing code, explain in 3 bullets what changed and how to test it.
- All code in this repo is new work for the Meta hackathon (Oct 26-Nov 4 2026).
- Always run Python as .venv\Scripts\python.exe (Windows). Run tests with: .venv\Scripts\python.exe -m pytest