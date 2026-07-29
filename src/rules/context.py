"""
CaseContext: one parsed view of a whole case, shared by every rule.

WHY THIS EXISTS
---------------
Checking a case file-by-file misses the interesting problems. The failures that
actually waste an engineer's day only appear when you hold several dictionaries
side by side:

    "turbulenceProperties says kOmegaSST, but 0/ has epsilon and no omega"
    "decomposeParDict says 4 subdomains, but there are 3 processor folders"
    "nut uses a wall function on a patch the mesh calls a plain 'patch'"

So instead of every rule re-parsing files, we parse once into this object and
let rules ask simple questions of it. Adding a new rule then costs a few lines
rather than a new parser.

Everything here is defensive: any missing or unparseable file yields None or an
empty collection, never an exception. A rule that cannot get its inputs simply
does not fire.
"""
import re
from functools import cached_property

from .. import openfoam_parser as ofp


def _as_float(value):
    """Best-effort float from a string like '0.7' or '1e-05'. None if not numeric."""
    try:
        return float(str(value).strip().split()[0])
    except (ValueError, IndexError, AttributeError):
        return None


def _last_float(value):
    """Last number in a string, e.g. 'nu [0 2 -1 0 0 0 0] 1e-05' -> 1e-05."""
    nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(value))
    try:
        return float(nums[-1]) if nums else None
    except ValueError:
        return None


def _parse_div_schemes(block: str) -> dict[str, str]:
    """
    Parse a divSchemes block, keeping each `div(...)` key whole.

    Handles nested parentheses, so div((nuEff*dev2(T(grad(U))))) survives intact
    instead of being truncated at the first closing bracket. Returns
    {"div(phi,U)": "bounded Gauss upwind", "default": "none", ...}.
    """
    text = ofp.strip_comments(block)
    out: dict[str, str] = {}
    i, n = 0, len(text)
    while i < n:
        start = text.find("div", i)
        if start == -1:
            break
        j = start + 3
        while j < n and text[j].isspace():
            j += 1
        if j >= n or text[j] != "(":          # not a div(...) entry
            i = start + 3
            continue
        depth, k = 0, j
        while k < n:                           # walk the balanced parentheses
            if text[k] == "(":
                depth += 1
            elif text[k] == ")":
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        key = "".join(text[start:k].split())   # collapse internal whitespace
        end = text.find(";", k)
        if end == -1:
            break
        out[key] = " ".join(text[k:end].split())
        i = end + 1

    # 'default' is a plain entry, so the generic parser handles it fine.
    default = ofp.scalar_entries(text).get("default")
    if default:
        out["default"] = default
    return out


class CaseContext:
    """
    A read-only, lazily-parsed view of one case.

    `files`  -- {relative_path: text}, as returned by read_case()/read_case_full()
    `survey` -- optional dict from case_survey.survey_case() (processor dirs,
                time dirs, logs). Rules that need it check `has_survey` first.
    """

    def __init__(self, files: dict[str, str], survey: dict | None = None,
                 case_path: str | None = None):
        self.files = files or {}
        self.survey = survey or {}
        self.case_path = case_path

    # --- basic access ---------------------------------------------------------
    @property
    def has_survey(self) -> bool:
        return bool(self.survey)

    def text(self, *relative_paths: str) -> str | None:
        """First of the given paths that exists, as text."""
        for rel in relative_paths:
            if rel in self.files:
                return self.files[rel]
        return None

    def has(self, *relative_paths: str) -> bool:
        return any(rel in self.files for rel in relative_paths)

    # --- controlDict ----------------------------------------------------------
    @cached_property
    def control(self) -> dict[str, str]:
        text = self.text("system/controlDict")
        return ofp.scalar_entries(text) if text else {}

    @cached_property
    def application(self) -> str:
        return (self.control.get("application") or "").strip().lower()

    @cached_property
    def delta_t(self):
        return _as_float(self.control.get("deltaT"))

    @cached_property
    def end_time(self):
        return _as_float(self.control.get("endTime"))

    @cached_property
    def write_interval(self):
        return _as_float(self.control.get("writeInterval"))

    @cached_property
    def adjustable_time_step(self) -> bool:
        return (self.control.get("adjustTimeStep") or "").strip().lower() in ("yes", "true", "on", "1")

    @cached_property
    def max_courant(self):
        return _as_float(self.control.get("maxCo"))

    # --- fvSchemes ------------------------------------------------------------
    @cached_property
    def ddt_scheme(self) -> str:
        text = self.text("system/fvSchemes")
        if not text:
            return ""
        block = ofp.content_of(text, "ddtSchemes") or ""
        return (ofp.scalar_entries(block).get("default") or "").strip().lower()

    @cached_property
    def div_schemes(self) -> dict[str, str]:
        """
        {full div key: scheme}, e.g. {"div(phi,U)": "bounded Gauss upwind"}.

        The generic scalar parser treats parentheses as separators, which would
        collapse every entry to the bare key "div" and silently overwrite them.
        Divergence schemes are important enough to parse properly, so we scan the
        block ourselves and keep the whole `div(...)` key intact.
        """
        text = self.text("system/fvSchemes")
        if not text:
            return {}
        block = ofp.content_of(text, "divSchemes")
        return _parse_div_schemes(block) if block else {}

    @cached_property
    def convection_div_schemes(self) -> dict[str, str]:
        """
        Only the CONVECTION terms, i.e. div(phi,...).

        The stress/diffusion term div((nuEff*dev2(T(grad(U))))) is correctly
        'Gauss linear' in virtually every case, so flagging it as unbounded
        would be a false alarm.
        """
        return {k: v for k, v in self.div_schemes.items() if "phi" in k}

    @cached_property
    def is_steady(self) -> bool:
        """Steady if the ddt scheme says so; fall back to the solver name."""
        if self.ddt_scheme:
            return "steadystate" in self.ddt_scheme
        return "simple" in self.application

    # --- fvSolution -----------------------------------------------------------
    @cached_property
    def solver_fields(self) -> set[str]:
        text = self.text("system/fvSolution")
        if not text:
            return set()
        block = ofp.content_of(text, "solvers")
        return set(ofp.subdict_names(block)) if block else set()

    @cached_property
    def relaxation(self) -> dict[str, str]:
        """Flattened relaxationFactors, including the fields{}/equations{} forms."""
        text = self.text("system/fvSolution")
        if not text:
            return {}
        block = ofp.content_of(text, "relaxationFactors")
        if not block:
            return {}
        entries = dict(ofp.scalar_entries(block))
        for sub in ("fields", "equations"):
            inner = ofp.content_of(block, sub)
            if inner:
                entries.update(ofp.scalar_entries(inner))
        return entries

    @cached_property
    def algorithm_block(self) -> str | None:
        """Which pressure-velocity coupling block is present: SIMPLE/PIMPLE/PISO."""
        text = self.text("system/fvSolution")
        if not text:
            return None
        for name in ("SIMPLE", "PIMPLE", "PISO"):
            if ofp.content_of(text, name) is not None:
                return name
        return None

    # --- turbulence -----------------------------------------------------------
    @cached_property
    def turbulence(self) -> dict:
        text = self.text("constant/turbulenceProperties", "constant/momentumTransport")
        return ofp.parse_turbulence(text) if text else {}

    @cached_property
    def turbulence_model(self) -> str:
        return (self.turbulence.get("model") or "").strip()

    @cached_property
    def simulation_type(self) -> str:
        return (self.turbulence.get("simulationType") or "").strip().lower()

    @cached_property
    def required_turbulence_fields(self) -> list[str]:
        return ofp.required_turbulence_fields(self.turbulence_model)

    # --- 0/ fields ------------------------------------------------------------
    @cached_property
    def zero_fields(self) -> dict[str, str]:
        """{bare field name: relative path} for whichever 0/ folder exists."""
        out = {}
        for rel in self.files:
            for prefix in ("0/", "0.orig/"):
                if rel.startswith(prefix):
                    out[rel[len(prefix):]] = rel
        return out

    @cached_property
    def field_patch_types(self) -> dict[str, dict[str, str]]:
        """
        {field_name: {patch_name: boundary_condition_type}}.

        This is what makes wall-function and BC-consistency rules possible, and
        the base parser does not provide it.
        """
        out: dict[str, dict[str, str]] = {}
        for name, rel in self.zero_fields.items():
            text = self.files.get(rel) or ""
            bf = ofp.content_of(text, "boundaryField")
            if not bf:
                continue
            per_patch = {}
            for patch in ofp.subdict_names(bf):
                inner = ofp.content_of(bf, patch) or ""
                bc_type = (ofp.scalar_entries(inner).get("type") or "").strip()
                if bc_type:
                    per_patch[patch] = bc_type
            out[name] = per_patch
        return out

    # --- mesh -----------------------------------------------------------------
    @cached_property
    def mesh_patches(self) -> dict[str, dict]:
        """{patch_name: {type, nFaces}} from constant/polyMesh/boundary."""
        text = self.text("constant/polyMesh/boundary")
        return ofp.parse_boundary(text) if text else {}

    @cached_property
    def wall_patches(self) -> set[str]:
        return {n for n, d in self.mesh_patches.items() if (d.get("type") or "").lower() == "wall"}

    # --- transport / thermophysical -------------------------------------------
    @cached_property
    def nu(self):
        text = self.text("constant/transportProperties")
        return _last_float(ofp.scalar_entries(text).get("nu", "")) if text else None

    @cached_property
    def is_compressible(self) -> bool:
        return self.has("constant/thermophysicalProperties") or "rho" in self.application

    # --- parallel -------------------------------------------------------------
    @cached_property
    def n_subdomains(self):
        text = self.text("system/decomposeParDict")
        if not text:
            return None
        return _as_float(ofp.scalar_entries(text).get("numberOfSubdomains"))

    @cached_property
    def decomposition_method(self) -> str:
        text = self.text("system/decomposeParDict")
        if not text:
            return ""
        return (ofp.scalar_entries(text).get("method") or "").strip().lower()

    @cached_property
    def processor_dirs(self) -> list[str]:
        return list(self.survey.get("processor_dirs") or [])

    # --- logs -----------------------------------------------------------------
    @cached_property
    def log_texts(self) -> dict[str, str]:
        """Any file that looks like a solver/utility log."""
        return {
            rel: text for rel, text in self.files.items()
            if rel.startswith("log") or rel.endswith(".log") or rel == "nohup.out"
        }
