# Conversational behavior: hidden 10-step process + two-stage prompts

This change makes the assistant behave like a normal chat app. The 10-step
diagnostic procedures are still run in full — but **internally only**. The user
sees a natural answer to their actual question, never the checklist.

## 1. Files changed / added
| File | Change |
|---|---|
| `src/intent.py` | **New.** Rule-based intent classification + automatic (multi-)skill selection + repeat-question detection. |
| `src/diagnoser.py` | **Rewritten** into two stages: `run_internal_analysis` (Stage 1, structured JSON) and `stream_chat_response` (Stage 2, natural prose), tied together by `run_full_turn`. Old `propose_edits` / `diagnose` kept. |
| `src/gui/main_window.py` | **Rewritten** flow: classify intent → optional internal diagnosis → streamed chat reply. Skill list defaults to **Auto**. Conversation memory added. |
| `src/gui/workers.py` | `StreamWorker.ok` now carries the `(reply, internal)` tuple. |
| `src/gui/progress_panel.py` | **Deleted** — it exposed the internal steps. Replaced by a simple rotating loading state. |
| `tests/test_pipeline.py` | **New.** Offline tests for intent routing, hidden step findings, and a full GUI turn. |

## 2. Architecture (two prompt stages)
```
user message + conversation context
        │
        ▼  classify_intent()  (rule-based, no API)
   intent + skills + needs_diagnosis + detail_level + focus
        │
        ▼  (only if needed)
  deterministic checks  +  RAG retrieval
        │
        ▼  STAGE 1  — internal diagnostic prompt
   Claude runs the 10-step procedures internally and returns
   ONLY structured JSON via the submit_internal_analysis tool.
        │  (InternalAnalysis object; step_findings kept hidden)
        ▼  STAGE 2  — chat response prompt
   Claude turns the internal JSON + the user's message + history
   into a natural, concise reply. No step numbers, no file dumps.
        │
        ▼
   polished chat answer  (streamed to the GUI)
```
The two stages use **separate system prompts** (`INTERNAL_SYSTEM_PROMPT` and
`CHAT_SYSTEM_PROMPT` in `src/diagnoser.py`). Stage 1 never writes prose; Stage 2
never re-runs the diagnosis.

## 3. Internal JSON schema (Stage 1 output)
Returned via the `submit_internal_analysis` tool and stored per conversation:
```json
{
  "selected_skills": [],
  "case_type": "",
  "files_inspected": [],
  "files_missing": [],
  "step_findings": [ { "skill": "", "step": 1, "finding": "", "evidence": "", "status": "" } ],
  "deterministic_findings": [],
  "retrieved_sources": [],
  "additional_ai_findings": [ { "finding": "", "reasoning": "", "confidence": "" } ],
  "ranked_causes": [ { "cause": "", "evidence": "", "confidence": "", "files": [] } ],
  "recommended_changes": [ { "file": "", "current": "", "proposed": "", "reason": "", "risk": "", "verification": "" } ],
  "confidence": "",
  "limitations": []
}
```
`step_findings` is **internal only**: `InternalAnalysis.public_dict()` drops it, so
Stage 2 never receives (and cannot render) the raw steps.

## 4. Intent classification & skill selection (`src/intent.py`)
`classify_intent(message, has_case, has_prior, prior_questions)` returns an `Intent`
with: `name`, `skills`, `needs_diagnosis`, `detail_level`, `focus`. Recognized intents:
`general_question`, `why_failing`, `targeted_inspect`, `is_change_safe`,
`whole_case_audit`, `recommendation`, `simpler_explanation`, `follow_up`,
`what_to_change_first`, `propose_edit`, `compare_options`.

- **Skill selection** is automatic and can be multiple: `select_skills()` maps
  keywords to skills, and divergence/pressure questions also pull in Boundary
  Condition + Numerical checks. "Check the whole case" runs all five.
- **`needs_diagnosis`** is `False` for follow-ups, "explain simpler", general
  questions, and re-asked questions — those reuse the stored analysis instead of
  re-running Stage 1 (prevents repeating the same full diagnosis).
- The GUI still has a skill dropdown; choosing a specific skill overrides Auto.

## 5. Conversation context
Each conversation stores `messages`, `last_internal` (the full internal object),
and `last_response`. On each turn the app passes to the prompts: recent history,
the previous internal analysis (to refine, not restart), the previous answer (for
"explain simpler"/follow-ups), and session notes (loaded case path, edits already
applied). Follow-ups build on this instead of starting from zero.

## 6. Response style
Stage 2 is instructed to answer the *specific* question, lead with the most likely
cause, recommend one smallest reversible change, show confidence words only when
useful, separate fact from inference, never claim it ran OpenFOAM, and use only the
relevant sections (e.g. "Most likely cause / Why / What to change first / What I
can't confirm"). Simple questions get a short natural answer, not a report.

## 7. GUI
No step panel. While working, a small bubble cycles a generic status
("Analyzing case files…", "Checking boundary conditions…", "Reviewing numerical
settings…", "Preparing response…"), then it is replaced by the streamed answer.
Safe editing is unchanged: propose → diff → approve → backup → undo.

## 8. Test instructions
```bash
# Windows PowerShell
$env:QT_QPA_PLATFORM="offscreen"; .\.venv\Scripts\python.exe tests\test_pipeline.py
```
The suite (all offline, no API) covers: whole-case audit, a single boundary
condition, a follow-up, "explain simpler", "what to change first", a file-edit
request, a general question with no case, multi-skill divergence, a case with no
solver log, a re-asked question that reuses the prior answer, hidden step findings,
and a full GUI turn with the network call faked.

To try it live: `python main.py`, set your key in **Settings**, **Select Case
Folder** → `examples/broken_case`, then ask e.g. *"why might this diverge?"*,
*"is my outlet BC right?"*, *"what should I change first?"*, *"explain that more
simply"*.
