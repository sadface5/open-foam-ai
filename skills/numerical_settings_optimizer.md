# SOP: Numerical Settings Optimizer

Use this procedure to tune the numerical settings in `system/fvSchemes` and
`system/fvSolution` for a better balance of **stability** and **convergence
speed** — without changing the physics of the case.

## What to look at
1. **Discretisation schemes (`system/fvSchemes`)**
   - `divSchemes` for convection are the biggest lever. Roughly, from most stable
     to most accurate: `Gauss upwind` (very stable, diffusive) → `Gauss
     linearUpwind grad(U)` (good compromise) → `Gauss limitedLinear 1` →
     `Gauss linear` (accurate, least stable). For steady runs, a `bounded` prefix
     (e.g. `bounded Gauss upwind`) helps.
   - `ddtSchemes` must match the run type: `steadyState` for SIMPLE, or a
     transient scheme (`Euler`, `backward`) for PIMPLE/PISO.
   - `snGradSchemes` / `laplacianSchemes`: on non-orthogonal meshes a `limited`
     corrected form (e.g. `limited corrected 0.33`) improves robustness.
2. **Linear solvers and controls (`system/fvSolution`)**
   - `relaxationFactors` (steady SIMPLE): lower values (more damping) are more
     stable but slower. If it diverges, lower them; if it is stable but slow, try
     raising them gradually.
   - `nNonOrthogonalCorrectors`: raise from 0 to 1–2 on non-orthogonal meshes.
   - `nCorrectors` / `nOuterCorrectors` (PIMPLE): more corrections per step add
     stability at extra cost.
   - `residualControl`: sensible convergence targets so a steady run stops when
     truly converged instead of running forever.
   - Solver `tolerance` / `relTol` for `p` and `U`: reasonable values keep each
     step accurate without wasting iterations.

## How to advise
- Recommend **one change at a time** so the user can see its effect, and always
  start with the smallest, most reversible tweak.
- Be explicit about the trade-off for each suggestion (e.g. "more stable but
  slower to converge", "more accurate but more likely to oscillate").
- Do not invent scheme or keyword names. Only suggest schemes you are confident
  exist in standard OpenFOAM; if a SPUMA-specific scheme might apply, defer to
  the retrieved knowledge and say if it is not present.
- If you cannot see `fvSchemes` or `fvSolution`, ask for them before advising.
