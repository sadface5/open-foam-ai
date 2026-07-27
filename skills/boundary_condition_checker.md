# SOP: Boundary Condition Checker

Use this procedure to review the `0/` initial-and-boundary-condition files for
consistency and completeness.

## What to check
1. **Every required field has a file.** For an incompressible RANS run you
   typically need `0/U`, `0/p`, and turbulence fields that match the model
   (`k` and `epsilon` for k-epsilon; `k` and `omega` for k-omega / SST), plus
   `nut`.
2. **Patch names match across files.** The `boundaryField` entries in each `0/`
   file must use the same patch names as `constant/polyMesh/boundary`. A typo or
   a missing patch is a very common failure.
3. **U and p boundary conditions form a consistent pair.** Common incompressible
   patterns:
   - Velocity inlet: `U` = `fixedValue`; `p` = `zeroGradient`.
   - Pressure outlet: `U` = `zeroGradient` or `inletOutlet`; `p` = `fixedValue`.
   - No-slip wall: `U` = `noSlip` (or `fixedValue (0 0 0)`); `p` = `zeroGradient`.
   Fixing both `U` and `p` to `fixedValue` on the same patch (or everywhere)
   over-constrains the problem.
4. **Dimensions.** For incompressible solvers, `p` is *kinematic* pressure with
   dimensions `[0 2 -2 0 0 0 0]` (m^2/s^2), NOT Pascals. A wrong `dimensions`
   line is a frequent mistake.
5. **Turbulence BCs and wall functions.** At walls, `k`, `epsilon`/`omega`, and
   `nut` usually use wall-function boundary conditions (e.g. `kqRWallFunction`,
   `epsilonWallFunction`, `omegaWallFunction`, `nutkWallFunction`). These must be
   present and consistent with the chosen model.

## Guidance
- If `constant/polyMesh/boundary` was not provided, you cannot fully verify patch
  names — say so and ask for it (or the patch list from `checkMesh`).
- For every suggested change, point to the exact file and the exact patch.
