# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project aims to
follow [Semantic Versioning](https://semver.org/).

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
