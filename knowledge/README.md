# Knowledge folder

This folder is the assistant's "library". Before asking Claude, the app searches
these files and sends only the most relevant snippets along with your question.
This technique is called **RAG** (Retrieval-Augmented Generation): it keeps
requests focused, cheaper, and grounded in *your* documentation.

## How to add your own knowledge
- Drop plain-text (`.txt`) or Markdown (`.md`) files into this folder.
- Write in short paragraphs separated by **blank lines** — each paragraph becomes
  one searchable snippet.
- Great things to add:
  - Your **SPUMA** documentation, solver notes, and tutorials. (Claude does not
    know SPUMA specifics unless you put them here.)
  - **Solved errors:** paste the exact error text and the fix that worked.
  - **Example cases** with a short explanation of the settings that matter.
- Sub-folders are fine — the search looks through them too.

## Tips for good retrieval
- One idea per paragraph retrieves better than one giant wall of text.
- Include the **real error wording** you saw. The search matches on keywords, so
  actual log phrasing helps it find the right snippet.
- This `README.md` is deliberately ignored by the search (it is instructions for
  you, not knowledge for the model).

The starter files here cover common OpenFOAM topics. Replace or extend them with
material specific to your own solvers and cases.
