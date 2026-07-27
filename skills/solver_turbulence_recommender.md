# SOP: Solver & Turbulence Model Recommender

Use this procedure to help choose an appropriate solver and turbulence model.
Recommend only solvers and models you are confident exist in standard OpenFOAM.
For SPUMA-specific solvers, rely on the retrieved knowledge and clearly say when
you are unsure.

## Questions that drive the choice
1. **Steady or transient?**
   - Steady-state → a SIMPLE-based solver (e.g. `simpleFoam`).
   - Time-accurate / transient → a PIMPLE- or PISO-based solver
     (e.g. `pimpleFoam`, `pisoFoam`).
2. **Incompressible or compressible?**
   - Low speed, roughly constant density → incompressible
     (`simpleFoam` / `pimpleFoam`).
   - Large density/temperature variation or high Mach number → compressible
     (`rhoSimpleFoam` / `rhoPimpleFoam`).
3. **Extra physics?** Free surface → `interFoam` (VOF). Buoyancy/heat →
   buoyant solvers. Simple laminar flow → `icoFoam`.

## Turbulence model guidance (RANS)
- **k-omega SST (`kOmegaSST`)** is a robust general-purpose default. It handles
  adverse pressure gradients and separation well and works with both
  wall-resolved and wall-functioned meshes.
- **k-epsilon (`kEpsilon`)** is common for high-Reynolds, free-shear and internal
  flows and generally relies on wall functions.
- **Wall treatment / y+.** High-Reynolds wall functions target a first-cell y+ of
  roughly 30–300; wall-resolved (low-Reynolds) approaches target y+ ≈ 1. The
  near-wall mesh must match the intended treatment.

## Guidance
- Base the recommendation on what the user actually tells you about their flow.
  If key facts are missing (steady vs transient, expected speed, geometry), ask
  for them instead of guessing.
- Remind the user that changing the solver or model is a *large* change: suggest
  it only when the current choice is clearly mismatched, and spell out what else
  must change with it (fields in `0/`, schemes, boundary conditions).
