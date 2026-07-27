# Roadmap

This is a rough, non-binding plan for where the project could go. Priorities may
change based on community feedback — suggestions and pull requests are welcome (see
[CONTRIBUTING.md](CONTRIBUTING.md)).

## Near term
- **Persistent conversation history** — save/reload past chats between sessions.
- **Expanded knowledge base** — more solved-error references and SPUMA-specific
  material.
- **Better OpenFOAM parsing** — handle more dictionary syntax (regex patches,
  `#include`, macros) in the deterministic checks.
- **Cost/usage display** — show approximate token usage per message.

## Medium term
- **Semantic RAG** — optional embedding-based retrieval as an alternative to the
  current keyword (TF-IDF) search, for better matching on paraphrased questions.
- **Richer mesh analysis** — parse `checkMesh` output more thoroughly and summarize
  quality trends.
- **Multi-file edit review** — apply a small, related set of edits together with a
  combined diff.
- **Cross-platform packaged builds** — macOS/Linux bundles in addition to the
  Windows `.exe`.

## Longer term / exploratory
- **Optional live verification hooks** — with explicit user opt-in, let the tool run
  read-only commands (e.g. `checkMesh`) locally to confirm a hypothesis. This must
  preserve the current safety model (no silent execution).
- **Case templates & guided setup** — help users start a new case correctly.
- **Localization** — support for languages other than English.

## Explicitly out of scope (for now)
- Automatically running solvers or modifying a case without user approval.
- Claiming that a fix "worked" without a real run.
