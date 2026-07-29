"""
Cross-file engineering consistency rules.

These are the checks that CANNOT be done one file at a time. Each one holds two
or more dictionaries side by side and looks for a contradiction between them --
the kind of mistake that produces a confusing solver error rather than an
obvious one.

Covered here:
    * turbulence model vs the fields actually present in 0/
    * turbulence model vs wall-function boundary conditions
    * wall functions on patches the mesh does not call walls
    * solver (application) vs the algorithm block in fvSolution
    * steady/transient agreement between controlDict and fvSchemes
    * compressible solver vs missing thermophysical properties
    * decomposeParDict vs the processor folders on disk
    * mesh presence vs a case that claims to have run
    * boundary condition types that disagree between related fields
"""
from . import (CATEGORY_BC, CATEGORY_MESH, CATEGORY_NUMERICS, CATEGORY_PARALLEL,
               CATEGORY_SOLVER, CATEGORY_THERMO, CATEGORY_TURBULENCE, CRITICAL,
               INFO, LIKELY, WARNING, finding, rule)

# Boundary condition types that require wall functions / a wall patch.
WALL_FUNCTION_MARKER = "wallfunction"

# Fields that belong to each turbulence family, used to spot leftovers.
KEPSILON_FIELDS = {"epsilon"}
KOMEGA_FIELDS = {"omega"}


@rule("turbulence-fields-mismatch", CATEGORY_TURBULENCE, CRITICAL,
      "Turbulence model needs fields that are missing")
def turbulence_fields_present(ctx):
    """kOmegaSST without 0/omega will stop the run before it starts."""
    required = ctx.required_turbulence_fields
    if not required:
        return None
    present = set(ctx.zero_fields)
    missing = [f for f in required if f not in present]
    if not missing:
        return None
    return finding(
        f"Turbulence model '{ctx.turbulence_model}' requires {', '.join(required)} "
        f"in the 0/ folder, but {', '.join(missing)} is missing.",
        files=["constant/turbulenceProperties", "0/"],
        evidence=[f"model={ctx.turbulence_model}",
                  f"0/ contains: {', '.join(sorted(present)) or 'nothing'}"],
        suggestion=f"Add 0/{missing[0]} with an appropriate initial value and boundary conditions.",
    )


@rule("turbulence-leftover-fields", CATEGORY_TURBULENCE, WARNING,
      "Fields present that belong to a different turbulence model")
def leftover_turbulence_fields(ctx):
    """
    A 0/epsilon left behind after switching to k-omega is harmless to the solver
    but almost always means the case was converted incompletely.
    """
    model = ctx.turbulence_model.lower()
    if not model:
        return None
    present = set(ctx.zero_fields)
    stray = set()
    if "komega" in model or "sst" in model:
        stray = present & KEPSILON_FIELDS
    elif "kepsilon" in model or "realizable" in model or "rng" in model:
        stray = present & KOMEGA_FIELDS
    if not stray:
        return None
    return finding(
        f"The case uses '{ctx.turbulence_model}' but 0/ still contains "
        f"{', '.join(sorted(stray))}, which belongs to a different model.",
        files=[ctx.zero_fields[f] for f in sorted(stray)],
        evidence=[f"model={ctx.turbulence_model}", f"stray fields: {', '.join(sorted(stray))}"],
        suggestion="Remove the unused field, or switch the model back if that was the intent.",
        confidence=LIKELY,
    )


@rule("wall-function-on-non-wall", CATEGORY_BC, CRITICAL,
      "Wall function applied to a patch that is not a wall")
def wall_function_patch_type(ctx):
    """
    Wall functions require the mesh patch to be of type 'wall'. Applying one to
    a plain 'patch' is a classic cause of a confusing startup failure.
    """
    if not ctx.mesh_patches:
        return None  # cannot verify without the authoritative patch list
    out = []
    for field_name, patches in ctx.field_patch_types.items():
        for patch, bc_type in patches.items():
            if WALL_FUNCTION_MARKER not in bc_type.lower():
                continue
            mesh_type = (ctx.mesh_patches.get(patch, {}).get("type") or "").lower()
            if mesh_type and mesh_type != "wall":
                out.append(finding(
                    f"0/{field_name} applies '{bc_type}' to patch '{patch}', but the mesh "
                    f"defines that patch as type '{mesh_type}', not 'wall'.",
                    files=[ctx.zero_fields.get(field_name, ""), "constant/polyMesh/boundary"],
                    evidence=[f"{field_name}.{patch}={bc_type}", f"mesh patch type={mesh_type}"],
                    suggestion=f"Either change the patch to type wall in the mesh, or use a "
                               f"non-wall-function condition on '{patch}'.",
                ))
    return out


@rule("wall-missing-wall-function", CATEGORY_BC, WARNING,
      "Wall patch without a wall-function condition for nut")
def wall_without_wall_function(ctx):
    """
    A RANS run with wall patches usually needs nut wall functions. Their absence
    is not always wrong (resolved-wall LES/low-Re) but is worth surfacing.
    """
    if not ctx.wall_patches or "nut" not in ctx.zero_fields:
        return None
    if "les" in ctx.simulation_type:
        return None  # wall-resolved LES legitimately omits them
    nut_patches = ctx.field_patch_types.get("nut", {})
    bare = [
        p for p in sorted(ctx.wall_patches)
        if p in nut_patches and WALL_FUNCTION_MARKER not in nut_patches[p].lower()
    ]
    if not bare:
        return None
    return finding(
        f"Wall patch(es) {', '.join(bare)} do not use a nut wall function "
        f"(found '{nut_patches[bare[0]]}').",
        files=[ctx.zero_fields.get("nut", "")],
        evidence=[f"wall patches: {', '.join(sorted(ctx.wall_patches))}",
                  f"nut.{bare[0]}={nut_patches[bare[0]]}"],
        suggestion="For a RANS case use nutkWallFunction (or nutUWallFunction) on wall patches.",
        confidence=LIKELY,
    )


@rule("solver-algorithm-mismatch", CATEGORY_SOLVER, WARNING,
      "Solver and fvSolution algorithm block disagree")
def solver_algorithm_block(ctx):
    """simpleFoam needs a SIMPLE block; pimpleFoam needs PIMPLE."""
    app, block = ctx.application, ctx.algorithm_block
    if not app or not block:
        return None
    expected = None
    if "simple" in app:
        expected = "SIMPLE"
    elif "pimple" in app:
        expected = "PIMPLE"
    elif "piso" in app or app == "icofoam":
        expected = "PISO"
    if not expected or block == expected:
        return None
    return finding(
        f"controlDict runs '{app}', which expects a '{expected}' block in fvSolution, "
        f"but the file defines '{block}' instead.",
        files=["system/controlDict", "system/fvSolution"],
        evidence=[f"application={app}", f"fvSolution block={block}"],
        suggestion=f"Rename the '{block}' block to '{expected}', or switch solver.",
    )


@rule("steady-transient-mismatch", CATEGORY_NUMERICS, WARNING,
      "Steady/transient settings contradict each other")
def steady_transient(ctx):
    """A steady solver with a transient ddt scheme (or the reverse)."""
    app, ddt = ctx.application, ctx.ddt_scheme
    if not app or not ddt:
        return None
    steady_app = "simple" in app
    steady_ddt = "steadystate" in ddt
    if steady_app == steady_ddt:
        return None
    return finding(
        f"application '{app}' is {'steady' if steady_app else 'transient'} but the ddtScheme "
        f"is '{ddt}' ({'steady' if steady_ddt else 'transient'}).",
        files=["system/controlDict", "system/fvSchemes"],
        evidence=[f"application={app}", f"ddtSchemes.default={ddt}"],
        suggestion="Make them agree: steadyState for simpleFoam, Euler/backward for transient runs.",
    )


@rule("compressible-missing-thermo", CATEGORY_THERMO, CRITICAL,
      "Compressible solver without thermophysical properties")
def compressible_needs_thermo(ctx):
    app = ctx.application
    if not app or not app.startswith(("rho", "buoyant", "cht", "sonic", "fire")):
        return None
    if ctx.has("constant/thermophysicalProperties"):
        return None
    return finding(
        f"'{app}' is a compressible/thermal solver but constant/thermophysicalProperties "
        f"was not found.",
        files=["constant/thermophysicalProperties"],
        evidence=[f"application={app}"],
        suggestion="Add constant/thermophysicalProperties describing the fluid's thermodynamics.",
    )


@rule("incompressible-with-thermo", CATEGORY_THERMO, INFO,
      "Incompressible solver alongside thermophysical properties")
def incompressible_with_thermo(ctx):
    app = ctx.application
    if not app or not ctx.has("constant/thermophysicalProperties"):
        return None
    if app.startswith(("rho", "buoyant", "cht", "sonic", "fire")):
        return None
    if not ctx.has("constant/transportProperties"):
        return None
    return finding(
        f"'{app}' is incompressible yet the case carries thermophysicalProperties as well as "
        f"transportProperties; one of them is probably unused.",
        files=["constant/thermophysicalProperties", "constant/transportProperties"],
        evidence=[f"application={app}"],
        suggestion="Remove whichever file the chosen solver does not read, to avoid confusion.",
        confidence=LIKELY,
    )


@rule("decomposition-mismatch", CATEGORY_PARALLEL, CRITICAL,
      "decomposeParDict disagrees with the processor folders on disk")
def decomposition_consistency(ctx):
    """numberOfSubdomains must match the number of processorN directories."""
    n = ctx.n_subdomains
    dirs = ctx.processor_dirs
    if n is None or not ctx.has_survey or not dirs:
        return None
    if int(n) == len(dirs):
        return None
    return finding(
        f"decomposeParDict requests {int(n)} subdomains but {len(dirs)} processor "
        f"folder(s) exist on disk.",
        files=["system/decomposeParDict"],
        evidence=[f"numberOfSubdomains={int(n)}", f"processor dirs={len(dirs)}"],
        suggestion="Re-run decomposePar after deleting the old processor* folders, "
                   "or set numberOfSubdomains to match.",
    )


@rule("decomposed-but-no-dict", CATEGORY_PARALLEL, WARNING,
      "Processor folders exist without a decomposeParDict")
def decomposed_without_dict(ctx):
    if not ctx.has_survey or not ctx.processor_dirs:
        return None
    if ctx.has("system/decomposeParDict"):
        return None
    return finding(
        f"{len(ctx.processor_dirs)} processor folder(s) exist but system/decomposeParDict "
        f"is missing, so the decomposition cannot be reproduced.",
        files=["system/decomposeParDict"],
        evidence=[f"processor dirs={len(ctx.processor_dirs)}"],
        suggestion="Restore decomposeParDict so decomposePar/reconstructPar behave predictably.",
    )


@rule("no-mesh", CATEGORY_MESH, CRITICAL,
      "Case has no usable mesh")
def mesh_missing(ctx):
    """Without polyMesh/faces there is nothing for a solver to run on."""
    if not ctx.has_survey:
        return None
    if ctx.survey.get("has_mesh"):
        return None
    return finding(
        "constant/polyMesh does not contain a generated mesh (faces/owner are absent).",
        files=["constant/polyMesh"],
        evidence=[f"mesh files present: {ctx.survey.get('mesh_present') or 'none'}"],
        suggestion="Run blockMesh (or snappyHexMesh) before attempting to solve.",
    )


@rule("patch-type-disagreement", CATEGORY_BC, WARNING,
      "Related fields disagree about a patch's nature")
def patch_type_disagreement(ctx):
    """
    If U treats a patch as an inlet (fixedValue) while p also fixes a value
    there, the case is over-constrained -- a very common divergence cause.
    """
    u_patches = ctx.field_patch_types.get("U", {})
    p_patches = ctx.field_patch_types.get("p", {})
    if not u_patches or not p_patches:
        return None
    out = []
    for patch, u_type in u_patches.items():
        p_type = p_patches.get(patch)
        if not p_type:
            continue
        if u_type.lower() == "fixedvalue" and p_type.lower() == "fixedvalue":
            out.append(finding(
                f"Patch '{patch}' fixes BOTH velocity and pressure (U={u_type}, p={p_type}), "
                f"which over-constrains the system.",
                files=[ctx.zero_fields.get("U", ""), ctx.zero_fields.get("p", "")],
                evidence=[f"U.{patch}={u_type}", f"p.{patch}={p_type}"],
                suggestion="Fix velocity at inlets with zeroGradient pressure, and fix pressure "
                           "at outlets with zeroGradient velocity.",
                confidence=LIKELY,
            ))
    return out


@rule("no-pressure-reference", CATEGORY_BC, WARNING,
      "Closed domain without a pressure reference")
def missing_pressure_reference(ctx):
    """
    If no patch fixes pressure, an incompressible solver needs pRefCell/pRefValue
    or the pressure level is undetermined.
    """
    p_patches = ctx.field_patch_types.get("p", {})
    if not p_patches or ctx.is_compressible:
        return None
    if any(t.lower() in ("fixedvalue", "totalpressure", "prghpressure") for t in p_patches.values()):
        return None
    text = ctx.text("system/fvSolution") or ""
    if "pRefCell" in text or "pRefValue" in text:
        return None
    return finding(
        "No patch fixes pressure and fvSolution defines no pRefCell/pRefValue, so the "
        "pressure level is unconstrained.",
        files=[ctx.zero_fields.get("p", ""), "system/fvSolution"],
        evidence=[f"p boundary types: {', '.join(sorted(set(p_patches.values())))}"],
        suggestion="Add pRefCell and pRefValue to the SIMPLE/PIMPLE block, or fix p on one patch.",
    )
