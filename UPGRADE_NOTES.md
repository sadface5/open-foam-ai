# Upgrade: structured 10-step diagnostics + deterministic checks + progress panel

> **Note (superseded in parts):** the 10-step procedures, deterministic checks, and
> RAG described here are still in use. However, the **visible progress panel** and the
> user-facing "standard structured answer" described below were later **removed**: the
> 10-step process now runs entirely internally and the assistant replies
> conversationally. For current behavior see [CHAT_BEHAVIOR.md](CHAT_BEHAVIOR.md).
> This file is kept as historical design/development notes.

This document explains the third upgrade. It preserves everything the app already
did and adds a required, methodical process on top.

## 1. Updated architecture (the pipeline)

Every **Analyze** now runs this pipeline (each stage is visible in the progress panel):

```
   your input + case folder
            │
            ▼
   ┌─────────────────────┐   the app inspects files ITSELF (no AI):
   │ deterministic checks │   missing files, patch coverage, missing turbulence
   └─────────────────────┘   fields, bad relaxation ranges, malformed dicts, ...
            │  (machine-verified findings)
            ▼
   ┌─────────────────────┐   TF-IDF search over /knowledge for relevant
   │   RAG retrieval      │   docs / solved cases / examples
   └─────────────────────┘
            │
            ▼
   ┌─────────────────────┐   system prompt + the skill's 10-step YAML procedure
   │   Claude (streamed)  │   + deterministic findings + files + knowledge + history
   └─────────────────────┘   → does the 10 steps FIRST, then broader reasoning
            │
            ▼
   ┌─────────────────────┐   pulls out per-step statuses, overall confidence,
   │   response parser    │   and the "Recommended first change"
   └─────────────────────┘
            │
            ▼
   structured diagnosis in chat  +  safe, diff-previewed edits (unchanged)
```

The design goal (as requested): **deterministic checks → required 10-step process
→ RAG → broader Claude reasoning → structured diagnosis → safe proposed edits.**
Claude is *guided* by the checklist, not *limited* to it.

## 2. New files
| File | Purpose |
|---|---|
| `skills/divergence_debugger.yaml` … `solver_model_recommender.yaml` (×5) | The structured 10-step procedures (data, not prompt). Each step has title, purpose, required/optional files, checks, evidence, severity/confidence rules, missing-info behavior, allowed recommendation categories. |
| `src/skill_defs.py` | Loads the YAML into dataclasses; renders the procedure for the prompt. |
| `src/openfoam_parser.py` | Best-effort OpenFOAM dictionary parser (patches, sections, key/values). |
| `src/deterministic.py` | The deterministic checks engine (runs before Claude). |
| `src/response_parser.py` | Parses Claude's standard response into sections/steps. |
| `src/gui/progress_panel.py` | The live progress panel (right dock). |
| `examples/broken_case/` | A deliberately-flawed case for testing. |

## 3. Modified files
| File | What changed |
|---|---|
| `src/diagnoser.py` | New system prompt (evidence labels, standard format, 10-step-first, "never claim it ran OpenFOAM"); layered user-message builder; `stream_diagnosis(...)` with a live `on_delta` callback. `diagnose()`/`propose_edits()` kept. |
| `src/case_reader.py` | Reads more files (mesh dicts, `polyMesh/boundary`, `0.orig/`); new `case_inventory()` that lists large files it deliberately does **not** read. |
| `src/skills.py` | Now delegates to the YAML loader (single source of truth) while keeping `list_skills` / `load_sop` / `SKILL_DESCRIPTIONS`. |
| `src/gui/workers.py` | Added `StreamWorker` (streams deltas off the UI thread). |
| `src/gui/chat.py` | `ChatBubble.set_text()` for live streaming; `ChatView.scroll_to_bottom()`. |
| `src/gui/main_window.py` | Orchestrates the pipeline, streams the reply, drives the progress dock. |
| `requirements.txt` | Added `PyYAML`. |

## 4. Evidence labels & the standard response format
Every finding is labeled **Confirmed / Likely / Possible / Unknown**. The reply is
Markdown with fixed headings (Selected skill, Case type, Files inspected/missing,
a per-step summary with Finding/Evidence/Status/Confidence, Main diagnosis, Overall
confidence, Evidence, Issues, **Additional AI findings beyond the checklist**,
Recommended first change, What cannot be verified without running OpenFOAM, etc.).
The exact template lives in `build_system_prompt()` in `src/diagnoser.py`; the
matching parser is `src/response_parser.py`.

## 5. Deterministic checks implemented
Missing key files · brace/dictionary malformation · patch coverage (each `0/`
field vs `polyMesh/boundary`, incl. case-mismatch) · missing turbulence fields for
the model · missing solver entries · missing `fvSchemes` sections · steady/transient
contradictions · out-of-range relaxation factors, non-positive `nu`/`deltaT`.
They are shown in the panel **and** handed to Claude as confirmed evidence.

## 6. GUI progress panel
Right-hand dock (toggle with the "Progress" button). Shows: selected skill, phase,
`Current step: N/10`, deterministic checks (with 🔴/🟠/🔵 severity), files inspected,
missing/not-read files, knowledge sources retrieved, and the AI status + final
overall confidence. Step numbers update live as the reply streams in.

## 7. Testing examples
- **Log-based:** paste `examples/sample_divergence_log.txt`, skill *Solver
  Divergence Debugger*.
- **Folder-based:** point *Select Case Folder* at `examples/broken_case` — the
  deterministic panel should immediately flag the missing `0/omega` (CRITICAL)
  before Claude runs.

Automated checks used during development (all passing): YAML loads (5×10 steps),
parser on boundary/field/turbulence, deterministic checks catch a planted patch
gap + missing field + bad relaxation, response parser extracts steps/confidence,
GUI builds headless, streaming step-detection and completion update the panel.

## 8. Migration plan (nothing breaks)
1. `pip install -r requirements.txt` (adds only PyYAML; everything else already installed).
2. The old public functions still exist (`list_skills`, `load_sop`, `diagnose`,
   `propose_edits`, `read_case`, `FileEditor`), so the legacy Streamlit `app.py`
   keeps working. `load_sop` now returns the YAML-derived 10-step text.
3. The desktop app (`python main.py`) automatically uses the new pipeline.
4. Model selector, RAG, safe editing, backups/undo, conversations, and Windows
   packaging (`build_windows_exe.bat`) are unchanged. The packaging script already
   copies `skills/` (now including the YAML files) next to the `.exe`.
5. To tune a procedure, edit the YAML in `skills/` — no Python changes needed.

## 9. Key design decisions
- **YAML for the procedures** so the 10 steps are data (editable, inspectable,
  reusable) instead of buried in one giant prompt.
- **Deterministic checks first** so the model receives verified facts and the user
  can see real progress before any AI call — reducing hallucination and making the
  process transparent.
- **Streaming** so the progress panel can show the live step position.
- **Checklist as a floor, not a ceiling:** the prompt explicitly requires broader
  reasoning, RAG comparison, and an "Additional AI findings beyond the checklist"
  section after the 10 steps.
- **Honesty guardrails:** the system prompt forbids claiming any run/verification
  (OpenFOAM/checkMesh/blockMesh/snappy/solvers), and mesh-quality items resolve to
  "Unknown — requires running checkMesh" when no report is supplied.
