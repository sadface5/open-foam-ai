# SOP: Mesh Doctor

Use this procedure when the problem is (or might be) the mesh: `checkMesh`
warnings or failures, or instability that looks mesh-driven.

## What to look for
1. **checkMesh output.** Key metrics and their typical trouble thresholds:
   - **Non-orthogonality:** a max above ~70° triggers warnings; very high values
     hurt both stability and accuracy.
   - **Skewness:** values above ~4 are flagged.
   - **Aspect ratio:** extremely large values (thousands and up) can slow or
     destabilise a run.
   - **Negative volume / negative face area:** these are fatal — the mesh is
     invalid and must be fixed, not worked around.
   - `***` markers and a `Failed N mesh checks` line indicate serious problems.
2. **Where the mesh came from** (blockMesh, snappyHexMesh, or imported). Do not
   assume — if it matters and is unknown, ask.

## Fixes, smallest first
1. **Tolerate a slightly bad mesh numerically:** if non-orthogonality is only
   moderately high, increasing `nNonOrthogonalCorrectors` in `system/fvSolution`
   (inside the SIMPLE/PIMPLE sub-dictionary) and using a limited `snGradSchemes`
   (e.g. `limited corrected 0.33`) in `system/fvSchemes` can let the run proceed
   while you improve the mesh.
2. **Fix the mesh itself:** for negative volumes or very high skewness, the mesh
   must be regenerated or repaired — better grading, moderate refinement, or
   improved snappyHexMesh quality controls.

## Guidance
- Never call a mesh "fine" or "broken" without the actual `checkMesh` output.
  If it was not provided, ask the user to run `checkMesh` and paste the summary.
- Be clear that numerical corrections (extra correctors, limited schemes) are
  stopgaps. A genuinely invalid mesh (negative volumes) has to be fixed.
