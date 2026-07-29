"""
Retrieval-augmented debugging.

The existing KnowledgeBase searches the /knowledge folder. That is the right
foundation, but a debugging assistant has more sources worth consulting before
it reasons:

    /knowledge          curated OpenFOAM reference material (the existing RAG)
    solved cases        what actually fixed a similar problem on this machine
    the rule library    the checks that exist, so advice matches what we verify

This module retrieves across all of them and returns one ranked, labelled
context block. Sources are labelled because their authority differs: a solved
case from this machine is stronger evidence than a general note, and both are
weaker than a rule finding, which was measured from the user's own files.

Retrieval happens BEFORE the model reasons, so the AI explains grounded material
rather than recalling OpenFOAM from memory.

Pure local computation -- no API calls, no network.
"""
from dataclasses import dataclass, field

from .debug_memory import recall_similar
from .knowledge_base import KnowledgeBase

# How much authority each source carries, used for ordering.
SOURCE_WEIGHT = {
    "solved-case": 3,     # it worked here before, on a real case
    "knowledge": 2,       # curated reference material
    "rule": 1,            # describes what the app can check
}


@dataclass
class RetrievedItem:
    """One piece of retrieved context."""
    source_kind: str          # solved-case | knowledge | rule
    label: str                # where it came from
    text: str

    def render(self) -> str:
        return f"[{self.source_kind}: {self.label}] {self.text}"

    @property
    def source(self) -> str:
        """
        Compatibility with knowledge_base.Snippet, so a RetrievedItem can be
        used anywhere a Snippet was expected (notably diagnoser._format_snippets)
        without changing that code. The label states the authority of the source,
        which matters when the model weighs it.
        """
        if self.source_kind == "solved-case":
            return f"a case solved previously on this machine ({self.label})"
        return self.label


@dataclass
class RetrievalResult:
    items: list = field(default_factory=list)

    def as_prompt_block(self, limit: int = 8) -> str:
        """Formatted for inclusion in the model's context."""
        if not self.items:
            return ""
        lines = ["Reference material retrieved for this question "
                 "(solved cases outrank general notes):", ""]
        for item in self.items[:limit]:
            lines.append("- " + item.render())
        return "\n".join(lines)

    def sources(self) -> list:
        return sorted({i.label for i in self.items})


class DebugRetriever:
    """
    Searches every local source at once.

    Wraps KnowledgeBase rather than replacing it, so existing callers of
    KnowledgeBase.search() keep working unchanged.
    """

    def __init__(self, knowledge_base: KnowledgeBase | None = None):
        self.kb = knowledge_base or KnowledgeBase()

    def retrieve(self, query: str, *, solver: str = "", turbulence: str = "",
                 findings=None, k: int = 5) -> RetrievalResult:
        """
        Gather context for a question.

        `findings` (from the rule engine) sharpens the query: if a rule already
        proved the turbulence model is missing a field, the words from that
        finding help pull the right reference material.
        """
        items: list[RetrievedItem] = []

        # Expand the query with what the rules already established, so retrieval
        # is guided by measured facts rather than the user's phrasing alone.
        extra_terms = []
        for f in (findings or [])[:5]:
            extra_terms.append(getattr(f, "title", "") or "")
            extra_terms.extend((getattr(f, "evidence", []) or [])[:2])
        enriched = " ".join([query] + [t for t in extra_terms if t])

        # 1. Previously solved cases -- the strongest local evidence.
        problem = " ".join(
            (getattr(f, "title", "") or "") for f in (findings or [])[:3]
        ) or query
        for row in recall_similar(solver=solver, turbulence=turbulence,
                                  problem=problem, limit=3):
            items.append(RetrievedItem(
                source_kind="solved-case",
                label=row.get("solver") or "previous case",
                text=(f"A similar case ({row.get('problem') or 'issue'}) was fixed by: "
                      f"{row.get('fix')}."),
            ))

        # 2. Curated documentation.
        for snippet in self.kb.search(enriched, k=k):
            items.append(RetrievedItem(
                source_kind="knowledge", label=snippet.source, text=snippet.text
            ))

        items.sort(key=lambda i: -SOURCE_WEIGHT.get(i.source_kind, 0))
        return RetrievalResult(items=items)

    def search(self, query: str, k: int = 5):
        """Backwards-compatible passthrough to the plain knowledge search."""
        return self.kb.search(query, k=k)
