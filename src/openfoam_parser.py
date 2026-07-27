"""
A small, best-effort parser for OpenFOAM dictionary files.

OpenFOAM's format is complex; this parser is intentionally lightweight. It is
"good enough" to power deterministic checks (patch lists, present sections,
simple key/value entries). Anything it cannot parse cleanly is simply left for
Claude to interpret -- we never pretend to fully understand a file we can't.
"""
import re


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)  # /* block comments */
    text = re.sub(r"//[^\n]*", " ", text)               # // line comments
    return text


def brace_balance(text: str) -> int:
    """Return (#open - #close) braces after removing comments. 0 means balanced."""
    text = strip_comments(text)
    return text.count("{") - text.count("}")


def content_of(text: str, key: str):
    """Return the text inside the braces of `key { ... }`, or None if not found."""
    text = strip_comments(text)
    m = re.search(rf"(?:^|[\s;]){re.escape(key)}\s*\{{", text)
    if not m:
        return None
    start = text.index("{", m.start()) + 1
    depth, i = 1, start
    while i < len(text) and depth > 0:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[start:i - 1] if depth == 0 else None


def _top_level_items(block: str):
    """
    Walk a dictionary block and yield its top-level items:
      ("dict", name)      for a sub-dictionary:  name { ... }
      ("scalar", [words]) for a simple entry:    key value1 value2 ;
    Parentheses are treated as separators, so vectors don't confuse it.
    """
    items = []
    words, cur = [], ""
    i, n = 0, len(block)

    def flush():
        nonlocal cur
        if cur.strip():
            words.append(cur.strip())
        cur = ""

    while i < n:
        c = block[i]
        if c == "{":
            flush()
            name = words[-1] if words else ""
            depth, j = 1, i + 1
            while j < n and depth > 0:
                if block[j] == "{":
                    depth += 1
                elif block[j] == "}":
                    depth -= 1
                j += 1
            items.append(("dict", name))
            words, i = [], j
            continue
        if c == ";":
            flush()
            if words:
                items.append(("scalar", words))
            words = []
            i += 1
            continue
        if c in " \t\r\n()":
            flush()
            i += 1
            continue
        cur += c
        i += 1
    return items


def subdict_names(block: str) -> list[str]:
    """Names of sub-dictionaries at the top level of a block."""
    return [name for kind, name in _top_level_items(block) if kind == "dict" and name]


def scalar_entries(block: str) -> dict[str, str]:
    """Simple `key value;` entries at the top level of a block."""
    out = {}
    for kind, words in _top_level_items(block):
        if kind == "scalar" and words:
            out[words[0]] = " ".join(words[1:])
    return out


# --------------------------------------------------------------------------
# Higher-level, file-specific parsers
# --------------------------------------------------------------------------
def parse_boundary(text: str) -> dict[str, dict]:
    """constant/polyMesh/boundary -> {patch_name: {type, nFaces}}."""
    text = strip_comments(text)
    result = {}
    for name in subdict_names(text):
        if name in ("FoamFile", ""):
            continue
        inner = content_of(text, name) or ""
        entries = scalar_entries(inner)
        result[name] = {"type": entries.get("type"), "nFaces": entries.get("nFaces")}
    return result


def parse_field(text: str) -> dict:
    """A 0/ field file -> {dimensions, internalField, patches}."""
    text = strip_comments(text)
    top = scalar_entries(text)
    bf = content_of(text, "boundaryField") or ""
    return {
        "dimensions": top.get("dimensions"),
        "internalField": top.get("internalField"),
        "patches": subdict_names(bf),
    }


def parse_turbulence(text: str) -> dict:
    """turbulenceProperties / momentumTransport -> {simulationType, model}."""
    text = strip_comments(text)
    top = scalar_entries(text)
    model = None
    for blockname in ("RAS", "LES", "RASProperties", "LESProperties"):
        inner = content_of(text, blockname)
        if inner:
            e = scalar_entries(inner)
            model = e.get("RASModel") or e.get("LESModel") or e.get("model")
            if model:
                break
    if not model:
        model = top.get("RASModel") or top.get("LESModel") or top.get("model")
    return {"simulationType": top.get("simulationType"), "model": model}


def required_turbulence_fields(model: str) -> list[str]:
    """Fields a given RANS model needs in the 0/ folder (best effort)."""
    if not model:
        return []
    m = model.lower()
    if "komega" in m or "sst" in m:
        return ["k", "omega", "nut"]
    if "kepsilon" in m or "kepsilon" in m.replace("-", "") or "rng" in m or "realizable" in m:
        return ["k", "epsilon", "nut"]
    if "spalart" in m or "nutilda" in m:
        return ["nuTilda", "nut"]
    return []
