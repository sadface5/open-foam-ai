# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project aims to
follow [Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-07-29

Evolves the assistant from a diagnostic chatbot into an investigating engineer.
Everything from 1.0.0 still works; all new capability is additive.

### Added
- **Complete case intelligence** — surveys the whole case (time directories,
  `processor*/`, logs, `postProcessing/`, uncurated dictionaries) instead of a
  fixed file list. Reads log *tails*, never mesh binaries.
- **Cross-file rule engine** — 31 expandable rules that catch contradictions only
  visible across dictionaries: turbulence model vs missing fields, wall functions
  on non-wall patches, over-constrained patches, `decomposeParDict` vs the
  processor folders on disk, and more.
- **Log intelligence** — residual trends per field, which field diverged *first*,
  Courant evolution, continuity-error growth, clipping frequency, crash signatures.
- **Mesh intelligence** — `checkMesh` metrics judged against practitioner
  thresholds, with a derived `nNonOrthogonalCorrectors` recommendation, and a
  cross-reference tying mesh quality to observed solver behaviour.
- **Working-vs-broken case comparison** — semantic diff, risk-ranked, ignoring
  cosmetic churn. Available from the toolbar.
- **OpenFOAM command integration** — detects installations across deb/rpm, tgz,
  source builds, conda, Spack, environment modules, WSL, Docker and blueCFD-Core;
  runs utilities under an allowlist, with parallel support via
  `mpirun -np N … -parallel`.
- **Autonomous debugging loop** — ranks hypotheses, designs experiments, runs
  read-only diagnostics, re-measures and evaluates, with a hard iteration cap.
  Available from the toolbar as **Auto-debug**.
- **Ranked root-cause hypotheses** with confidence, evidence, triggering rules and
  validation steps.
- **Session memory and a learned-case database** — never repeats a fix that
  already failed; records what worked so future diagnoses can recall it.
- **Versioned file editing** — per-file history, rollback to any earlier version,
  and the reason, confidence and evidence behind every change.

### Fixed
- **Backups could be destroyed.** Backup filenames used second-granularity
  timestamps, so two edits to the same file within one second produced the same
  filename and the second overwrote the first. The original content was lost and
  a second undo restored the wrong data. Backups are now never overwritten.
- `divSchemes` entries all collapsed to the key `div`, so only the last survived.
- The stress term `div((nuEff*dev2(T(grad(U)))))` was wrongly reported as an
  unbounded convection scheme.

### Changed
- `SECURITY.md` and `README.md` now describe command execution, the allowlist and
  the write gate, replacing the earlier claim that the tool never executes
  anything.

## [1.0.0] - 2026-07-27

First public release.

### Added
- **Desktop GUI** (PySide6): chat-style interface with conversation history, a
  model selector, case-folder selection, and file attachment.
- **Five diagnostic skills**, each defined by a structured 10-step procedure stored
  as editable YAML in `skills/`:
  Solver Divergence Debugger, Mesh Doctor, Boundary Condition Checker,
  Numerical Settings Optimizer, and Solver & Turbulence Model Recommender.
- **Automatic intent classification and skill selection** — the app picks the
  relevant skill(s) from the user's question and can combine several.
- **Two-stage reasoning**: an internal structured analysis (the 10 steps run
  hidden) followed by a natural, conversational reply.
- **Deterministic pre-checks** (no AI) for missing files, patch-coverage mismatches,
  missing turbulence fields, missing solver entries, missing scheme sections,
  steady/transient contradictions, out-of-range relaxation factors, and malformed
  dictionaries.
- **Local knowledge base with RAG** (TF-IDF) and starter OpenFOAM reference notes in
  `knowledge/`.
- **Safe file editing**: diff preview, explicit user approval, automatic timestamped
  backups, path-traversal protection, and one-click undo.
- **Model tiers** (Fast / Balanced / Deep / Agentic) configurable in
  `src/config.py`.
- **Windows `.exe` packaging** via `build_windows_exe.bat` (PyInstaller).
- **Offline test suite** (`tests/test_pipeline.py`) covering intent routing, skill
  selection, hidden step findings, and a full GUI turn with the network mocked.
- Example assets: a sample solver log and a deliberately-broken case
  (`examples/broken_case`).

### Notes
- The assistant cannot run OpenFOAM and never claims a fix is verified.
- Requires an Anthropic API key; case-file excerpts are sent to the Claude API
  during a diagnosis.

[1.0.0]: https://github.com/sadface5/open-foam-ai/releases/tag/v1.0.0
