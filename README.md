# 🌀 OpenFOAM / SPUMA CFD Debugging Assistant

An AI-powered desktop assistant that helps you **debug OpenFOAM / SPUMA CFD cases**.
Ask it a plain-English question about your case ("why is this diverging?", "is my
outlet boundary condition right?", "what should I change first?") and it inspects
your case files, runs a rigorous internal diagnostic procedure, and replies with a
clear, ranked answer — plus optional, **safe** file edits that you review and
approve before anything is written.

It runs **locally** and uses the **Anthropic Claude API** for reasoning.

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![GUI](https://img.shields.io/badge/GUI-PySide6-green)

> **Status:** working application, first public release. It is a decision-support
> tool: it **suggests** changes and never runs OpenFOAM itself.

> 📝 **Using the assistant?** Please take the 3–5 minute
> [Beta Feedback Survey](https://formhug.ai/f/jHKu9K) — it directly shapes what gets
> built next (see [ROADMAP.md](ROADMAP.md)).

---

## Table of contents
- [Overview](#overview)
- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Setup (API key)](#setup-api-key)
- [Usage](#usage)
- [Supported OpenFOAM files](#supported-openfoam-files)
- [Building a Windows .exe](#building-a-windows-exe)
- [Current limitations](#current-limitations)
- [Future plans](#future-plans)
- [Contributing](#contributing)
- [Security & privacy](#security--privacy)
- [License](#license)

---

## Overview

CFD cases fail for many small reasons — a missing field, an over-constrained
pressure boundary, an aggressive scheme, a time step that is too large. This tool
acts like a methodical reviewer: it reads the relevant files, checks them against
known good practice, retrieves relevant documentation, and explains the most likely
problem in language a learner can follow.

The assistant is built around **five diagnostic skills**, each defined by a
10-step troubleshooting procedure. You do **not** pick a skill manually — the app
reads your question and automatically runs the relevant skill(s). The 10-step
procedure runs entirely **behind the scenes**; you only see a natural, focused
answer.

The five skills:

1. **Solver Divergence Debugger** — the run blew up (`nan`/`inf`, floating-point error, exploding residuals).
2. **Mesh Doctor** — `checkMesh` warnings or mesh-driven instability.
3. **Boundary Condition Checker** — reviews the `0/` initial & boundary conditions.
4. **Numerical Settings Optimizer** — tunes `fvSchemes` / `fvSolution` for stability and convergence.
5. **Solver & Turbulence Model Recommender** — helps choose a solver and turbulence model.

---

## Features

- **Chat-style desktop GUI** (PySide6) with conversation history.
- **Automatic intent detection & skill selection** — ask naturally; the app picks
  the right skill(s), and can combine several when a problem crosses categories.
- **Hidden 10-step diagnostic procedure** per skill (stored as editable YAML) that
  runs internally, so answers stay concise instead of dumping a checklist.
- **Deterministic pre-checks** run locally (no AI) to catch things like missing
  fields, patch-coverage mismatches, out-of-range relaxation factors, and
  malformed dictionaries — these become verified evidence for the AI.
- **Local knowledge base with RAG** (keyword/TF-IDF search) so answers are grounded
  in documentation you can extend yourself.
- **Safe file editing** — the assistant proposes complete file changes; you see a
  colored diff and a reason, and nothing is written until you click **Apply**. Every
  edit is backed up automatically, with one-click **undo**.
- **Model selector** (Fast / Balanced / Deep / Agentic) to trade speed vs. depth.
- **Read-only by default** — it never modifies your case unless you approve a diff.

---

## How it works

Each message flows through this pipeline:

```
your message
   │
   ▼  intent classification (rule-based, local) — what are you asking? which skills?
   │
   ▼  (only when a diagnosis is needed)
   │     deterministic checks  +  RAG knowledge retrieval
   │        │
   │        ▼  STAGE 1 (internal): Claude runs the 10-step procedure(s) and returns
   │           a structured JSON result — never shown to you.
   │
   ▼  STAGE 2 (chat): Claude turns that internal result + your message + history
      into a natural, ranked reply.
   │
   ▼  optional: propose a safe edit → diff → you approve → backup → apply → undo
```

Follow-ups, "explain that more simply", and general questions **reuse** the previous
analysis instead of re-running everything, so the assistant behaves like a normal
chat rather than repeating a full report each time.

The 10-step procedures live in [`skills/*.yaml`](skills) and the reference material
lives in [`knowledge/`](knowledge) — both are plain text you can edit without
touching code.

---

## Requirements

- **Python 3.10 or newer**
- An **Anthropic API key** ([console.anthropic.com](https://console.anthropic.com/))
- Runs on **Windows, macOS, and Linux** from source. (The one-click `.exe` build is
  Windows-only; it was developed and tested primarily on Windows.)

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/sadface5/open-foam-ai.git
cd open-foam-ai

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

> Windows tip: if PowerShell blocks the activate script, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then try again.

---

## Setup (API key)

You need an Anthropic API key. There are two easy ways to provide it:

**Option A — in the app (recommended):** run the app (below), click **⚙ Settings**,
paste your key, and click **Save key**. It is written to a local `.env` file that is
**git-ignored**.

**Option B — manually:** copy `.env.example` to `.env` and put your key inside:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Your `.env` file is listed in `.gitignore` and will not be committed. **Never commit
a real key.**

---

## Usage

Run the desktop app:

```bash
python main.py
```

Then:

1. Click **📁 Select Case Folder** and choose your OpenFOAM/SPUMA case (the folder
   that contains `system/`, `constant/`, and `0/`). You can also **📎 Attach** a
   solver log or `checkMesh` output.
2. Type a question in plain English and press **➤ Send** (or `Ctrl+Enter`).
   Examples:
   - *"Why might this case fail to run?"*
   - *"Is my outlet boundary condition correct?"*
   - *"What should I change first?"*
   - *"Which turbulence model should I use?"*
   - *"Explain that more simply."*
3. The **Mode** dropdown (top-left) chooses depth vs. cost. The **skill** list on the
   left is set to **Auto** by default; you can override it to force a specific skill.
4. To let the assistant draft a change, click **✎ Propose Edits**. Review the diff,
   then **✔ Apply this change** — the original is backed up first, and **↩ Undo last
   change** restores it.

Want to try it immediately? Point **Select Case Folder** at
[`examples/broken_case`](examples/broken_case) (which has a deliberately missing
field) and ask *"why might this diverge?"*.

### Model selector

Model IDs live in [`src/config.py`](src/config.py) under `MODELS` — edit them there
if Anthropic updates a model name.

| Mode | Model family | Best for |
|---|---|---|
| **Fast** | Claude Haiku | Quick questions and short explanations |
| **Balanced** | Claude Sonnet | Everyday debugging (good default) |
| **Deep** | Claude Opus | Hard convergence / mesh / solver problems |
| **Agentic** | Claude Fable | Long, thorough, multi-part analysis |

---

## Supported OpenFOAM files

The assistant reads these text files from a case folder (missing files are simply
skipped):

- **`system/`** — `controlDict`, `fvSchemes`, `fvSolution`, `blockMeshDict`,
  `snappyHexMeshDict`, `surfaceFeatureExtractDict`, `meshQualityDict`
- **`constant/`** — `transportProperties`, `turbulenceProperties`,
  `momentumTransport`, `physicalProperties`, `thermophysicalProperties`, `g`,
  `polyMesh/boundary`
- **`0/`** (or `0.orig/`) — every field file present (`U`, `p`, `k`, `epsilon`,
  `omega`, `nut`, `nuTilda`, `T`, phase/species fields, …)

Large mesh files (`polyMesh/points`, `faces`, `owner`, `neighbour`) are **detected
but not read** (they can be enormous). Geometry files under `constant/triSurface/`
are noted. Solver logs and `checkMesh` output can be **pasted or attached**.

---

## Building a Windows .exe

With the virtual environment active:

```powershell
.\build_windows_exe.bat
```

This produces `dist\OpenFOAM-AI.exe` and copies the editable `skills\` and
`knowledge\` folders next to it. To share the app, distribute the whole `dist\`
folder; each user sets their own API key via **Settings**.

---

## Current limitations

- **It cannot run OpenFOAM.** It does not execute solvers, `checkMesh`, `blockMesh`,
  or `snappyHexMesh`, and never claims a fix is "verified" — only a real run can
  confirm that. Mesh-quality questions need a `checkMesh` report to be conclusive.
- **Requires internet + an Anthropic API key**, and each diagnosis costs a small
  amount of API usage.
- **The OpenFOAM dictionary parser used for deterministic checks is best-effort**;
  unusual syntax may be skipped (the AI still reads the raw file).
- **"SPUMA" specifics are not built in.** Add SPUMA documentation to `knowledge/`
  so the assistant can use it.
- **Conversation history is per-session** and is not saved to disk between runs.
- **Only English** prompts/answers have been tested.

---

## Future plans

See [ROADMAP.md](ROADMAP.md). Highlights: optional live verification hooks, semantic
(embedding-based) RAG, richer mesh analysis, persistent conversation history, and a
cross-platform packaged build.

---

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for how to
set up a dev environment, run the tests, and open a pull request. By participating
you agree to keep discussions respectful and constructive.

---

## Security & privacy

- **Never commit API keys.** `.env` is git-ignored; keep real keys out of
  `.env.example`. See [SECURITY.md](SECURITY.md).
- **Your data is sent to Anthropic.** When you run a diagnosis, relevant excerpts of
  your case files and logs are sent to the Claude API for analysis. Do **not** use
  this tool on confidential, export-controlled, or otherwise restricted data.
- The app only **reads** your case files and only **writes** after you approve a diff.

---

## License

Released under the **MIT License** — see [LICENSE](LICENSE).

---

*OpenFOAM® is a registered trademark of OpenCFD Ltd. This project is an independent,
community tool and is not affiliated with, endorsed by, or connected to OpenCFD Ltd
or the OpenFOAM Foundation.*
