"""
OpenFOAM / SPUMA CFD Debugging Assistant  --  a simple, local GUI.

Run it from the project folder with:

    streamlit run app.py

It opens in your web browser but runs entirely on your own computer.
"""
import streamlit as st

from src.case_reader import read_case
from src.diagnoser import diagnose
from src.knowledge_base import KnowledgeBase
from src.skills import list_skills, load_sop

# --- Page setup ---------------------------------------------------------------
st.set_page_config(
    page_title="OpenFOAM/SPUMA Debugging Assistant",
    page_icon="🌀",
    layout="wide",
)
st.title("🌀 OpenFOAM / SPUMA Debugging Assistant")
st.caption("An AI helper that *suggests* (never applies) fixes for your CFD cases.")


# --- Build the knowledge base once, then reuse it across clicks ----------------
# @st.cache_resource means Streamlit builds this a single time and keeps it in
# memory, instead of re-reading /knowledge on every button press.
@st.cache_resource(show_spinner="Indexing the /knowledge folder...")
def get_knowledge_base() -> KnowledgeBase:
    return KnowledgeBase()


kb = get_knowledge_base()

# --- Short descriptions to help the user pick the right skill ------------------
SKILL_HELP = {
    "Solver Divergence Debugger": "The run blew up: floating point error, `nan`/`inf`, or exploding residuals.",
    "Mesh Doctor": "checkMesh warnings, bad cells, or instability that looks mesh-related.",
    "Boundary Condition Checker": "You suspect the 0/ boundary or initial conditions are wrong or inconsistent.",
    "Solver & Turbulence Model Recommender": "You're unsure which solver or turbulence model to use.",
    "Case Setup Auditor": "A general 'is my whole case set up correctly?' review.",
}

# --- Inputs -------------------------------------------------------------------
skill = st.selectbox("1. Choose a diagnostic skill", list_skills())
st.info(SKILL_HELP.get(skill, ""))

user_input = st.text_area(
    "2. Paste your error log or describe the problem",
    height=220,
    placeholder="Paste the solver output / error messages here, or describe what went wrong...",
)

case_path = st.text_input(
    "3. (Optional) Path to your case folder",
    placeholder=r"e.g.  C:\Users\you\myCase   or   /home/you/myCase",
)

go = st.button("🔍 Diagnose", type="primary")

# --- Run the diagnosis when the button is clicked -----------------------------
if go:
    if not user_input.strip() and not case_path.strip():
        st.warning("Please paste an error log OR provide a case folder path (or both).")
        st.stop()

    # Step 1: read the case files, if a folder path was given.
    case_files: dict[str, str] = {}
    if case_path.strip():
        try:
            case_files = read_case(case_path)
        except Exception as e:
            st.error(f"Could not read the case folder: {e}")
            st.stop()
        if not case_files:
            st.warning(
                "The folder was found, but none of the expected OpenFOAM files "
                "were in it (system/, constant/, 0/). Continuing with your text only."
            )

    # Step 2: retrieve relevant knowledge. We search using the user's text plus
    # the chosen skill name, so the snippets match the topic.
    query = f"{skill}. {user_input}"
    snippets = kb.search(query)

    # Step 3: load the chosen skill's SOP procedure.
    try:
        sop = load_sop(skill)
    except Exception as e:
        st.error(f"Could not load the skill instructions: {e}")
        st.stop()

    # Step 4: ask Claude.
    try:
        with st.spinner("Claude is analysing your case... (this can take ~30 seconds)"):
            result = diagnose(sop, user_input, case_files, snippets)
    except Exception as e:
        st.error(f"Something went wrong talking to the Claude API:\n\n{e}")
        st.stop()

    # --- Show the diagnosis ---------------------------------------------------
    st.markdown("---")
    st.markdown(result)

    # --- Transparency: show exactly what the AI was given ---------------------
    st.markdown("---")
    st.subheader("What the assistant looked at")

    with st.expander("📄 Files read from your case folder"):
        if case_files:
            for rel_path in case_files:
                st.write(f"- `{rel_path}`")
        else:
            st.write("No case-folder files were used.")

    with st.expander("📚 Knowledge snippets used (from the RAG search)"):
        if snippets:
            for i, s in enumerate(snippets, start=1):
                st.markdown(f"**[{i}] from `{s.source}`**")
                st.text(s.text)
        else:
            st.write(
                "No knowledge snippets matched your question. Add more documents "
                "to the /knowledge folder to improve future answers."
            )
