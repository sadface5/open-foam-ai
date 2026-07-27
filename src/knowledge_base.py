"""
A small Retrieval-Augmented Generation (RAG) helper.

Plain English: instead of sending Claude the ENTIRE /knowledge folder every time
(slow and expensive), we find just the few snippets most relevant to the user's
question and send only those.

We do this with TF-IDF -- a classic keyword-weighting technique. It runs entirely
on your computer: no extra API key and no internet needed for the search step.
"""
from dataclasses import dataclass
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .config import KNOWLEDGE_DIR, TOP_K_SNIPPETS


@dataclass
class Snippet:
    """One searchable chunk of knowledge, plus the file it came from."""
    source: str
    text: str


def _load_snippets() -> list[Snippet]:
    """Read every .md/.txt file in /knowledge and split it into snippets."""
    snippets: list[Snippet] = []
    if not KNOWLEDGE_DIR.is_dir():
        return snippets

    for path in sorted(KNOWLEDGE_DIR.glob("**/*")):  # ** = search subfolders too
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        if path.name.lower() == "readme.md":
            continue  # the README is instructions for humans, not knowledge
        content = path.read_text(encoding="utf-8", errors="replace")
        # Split on blank lines so each paragraph becomes one snippet.
        for chunk in content.split("\n\n"):
            chunk = chunk.strip()
            if len(chunk) >= 40:  # skip tiny fragments (e.g. a lone heading)
                snippets.append(Snippet(source=path.name, text=chunk))
    return snippets


class KnowledgeBase:
    """Builds a searchable index over /knowledge once, then answers queries fast."""

    def __init__(self) -> None:
        self.snippets = _load_snippets()
        if self.snippets:
            # Learn the vocabulary of all snippets and turn each into a vector.
            self._vectorizer = TfidfVectorizer(stop_words="english")
            self._matrix = self._vectorizer.fit_transform(
                [s.text for s in self.snippets]
            )
        else:
            self._vectorizer = None
            self._matrix = None

    def search(self, query: str, k: int = TOP_K_SNIPPETS) -> list[Snippet]:
        """Return the k snippets most relevant to the query (best first)."""
        if not self.snippets or self._vectorizer is None or not query.strip():
            return []
        query_vec = self._vectorizer.transform([query])
        scores = cosine_similarity(query_vec, self._matrix)[0]
        ranked = scores.argsort()[::-1][:k]  # indices of highest scores, best first
        # Keep only snippets that actually share some keywords with the query.
        return [self.snippets[i] for i in ranked if scores[i] > 0.0]
