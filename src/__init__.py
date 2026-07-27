"""
The 'src' package holds the building blocks of the assistant:

- config.py          -> settings (API key, model name, folder paths)
- skills.py          -> loads the 5 diagnostic SOP prompts from /skills
- case_reader.py     -> reads files from an OpenFOAM/SPUMA case folder
- knowledge_base.py  -> searches the /knowledge folder (the "RAG" step)
- diagnoser.py       -> builds the prompt and calls the Claude API

app.py (in the project root) ties them together into a GUI.
"""
