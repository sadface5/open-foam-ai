# SOP: Solver Divergence Debugger

Use this procedure when a run "blows up": residuals grow instead of shrinking,
values become `nan` / `inf`, the solver reports a floating point exception
(`sigFpe`), or you see `bounding` warnings on turbulence fields.

## What to look for in the evidence
1. **Courant number.** In the log, look for `Courant Number mean: ... max: ...`.
   For transient solvers (PIMPLE/PISO), a max Courant number well above ~1, or
   one that climbs every time step, strongly suggests the time step is too large.
2. **Residual behaviour.** A rising `Initial residual` for `p` (pressure) across
   iterations or time steps is the classic signature of divergence.
3. **Bounding warnings.** Lines like `bounding k`, `bounding epsilon`, or
   `bounding omega` mean turbulence quantities went negative and were clipped.
   That is usually a *symptom* pointing back to the mesh, BCs, or time step.
4. **The crash line.** `Floating point exception`, `sigFpe`, `nan`, or `inf`
   confirm a genuine blow-up rather than a clean, intended stop.

## Order of suspicion (cheapest, most reversible fixes first)
1. **Time step / Courant number (transient):** reduce `deltaT` in
   `system/controlDict`; or if `adjustTimeStep yes;`, lower `maxCo`
   (for example from 1 to 0.5).
2. **Under-relaxation (steady, SIMPLE):** lower `relaxationFactors` in
   `system/fvSolution` (e.g. reduce `p` and `U` factors) to stabilise, then
   raise them again once the run is converging.
3. **Initial / boundary fields:** unrealistic initial values in `0/`, or an
   inlet velocity far from the expected magnitude, can start a blow-up.
4. **Discretisation schemes (`system/fvSchemes`):** very aggressive `divSchemes`
   (e.g. pure `Gauss linear` on convection) can be unstable. A more robust
   choice such as `bounded Gauss upwind` or `Gauss limitedLinear 1` is safer
   while diagnosing.
5. **Mesh quality:** if nothing above helps, poor cells (high non-orthogonality
   or skewness) may be the root cause. Recommend running `checkMesh` and, if
   needed, switching to the Mesh Doctor skill.

## Guidance
- Recommend the smallest change likely to stabilise the run FIRST, and tell the
  user how to know it worked (residuals falling, Courant number in range, no
  more bounding warnings).
- If the log alone is not enough (e.g. you cannot see the time step or schemes),
  say which files you need: usually `system/controlDict`, `system/fvSolution`,
  and `system/fvSchemes`.
