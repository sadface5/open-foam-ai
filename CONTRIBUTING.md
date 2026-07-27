# Contributing

Thanks for your interest in improving the OpenFOAM / SPUMA CFD Debugging Assistant!
This project aims to stay **beginner-friendly and readable**, so contributions that
keep the code simple and well-commented are especially welcome.

## Ways to contribute

- **Report bugs** or confusing behavior via GitHub Issues (include what you asked,
  what happened, and — if relevant — the OpenFOAM version and a minimal case).
- **Improve the knowledge base** (`knowledge/`): add accurate OpenFOAM/SPUMA notes,
  solved errors, or reference material. This is the single easiest way to make the
  assistant smarter. See `knowledge/README.md` for the format.
- **Tune the diagnostic procedures** (`skills/*.yaml`): the 10-step procedures are
  plain YAML — no code needed.
- **Fix bugs or add features** in the Python code.
- **Improve documentation.**

## Development setup

```bash
git clone https://github.com/sadface5/open-foam-ai.git
cd open-foam-ai
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Run the app with `python main.py` (you will need an Anthropic API key — see the
README's *Setup* section).

## Running the tests

The test suite is **offline** (it does not call the API):

```bash
# Windows (PowerShell)
$env:QT_QPA_PLATFORM="offscreen"; python tests\test_pipeline.py
# macOS / Linux
QT_QPA_PLATFORM=offscreen python tests/test_pipeline.py
```

`QT_QPA_PLATFORM=offscreen` lets the Qt GUI construct without a display. All tests
should pass before you open a pull request. If you change intent routing, skill
selection, or the internal analysis schema, please add or update a test.

A quick syntax check for every module:

```bash
python -m py_compile main.py app.py src/*.py src/gui/*.py tests/*.py
```

## Project layout (short version)

- `main.py` — desktop app entry point
- `src/` — backend: `intent.py`, `diagnoser.py` (two-stage prompts), `deterministic.py`,
  `openfoam_parser.py`, `knowledge_base.py` (RAG), `skill_defs.py`, `case_reader.py`,
  `file_editor.py` (safe edits), `config.py`
- `src/gui/` — PySide6 interface
- `skills/*.yaml` — the five 10-step diagnostic procedures (data, not code)
- `knowledge/` — the RAG reference library
- `examples/` — a sample log and a deliberately-broken case for testing

## Style guidelines

- Target **Python 3.10+** and keep code readable — clear names and short comments
  over cleverness. This project is used by people learning both CFD and programming.
- Match the surrounding style; keep functions small.
- Don't hard-code absolute paths or secrets.
- Keep the safety guarantees intact: the app must never write to a user's case
  without an approved diff and a backup, and must never claim it ran OpenFOAM.

## Pull requests

1. Create a branch from `main`.
2. Make your change and run the tests + `py_compile`.
3. Describe **what** changed and **why**. Screenshots help for GUI changes.
4. Keep PRs focused — one topic per PR is easier to review.

## Code of conduct

Please be respectful and constructive. Assume good intent, keep feedback kind, and
help newcomers. Harassment or discrimination of any kind is not tolerated.
