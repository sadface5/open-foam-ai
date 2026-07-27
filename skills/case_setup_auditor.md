# SOP: Case Setup Auditor

Use this procedure for a broad "is my case set up correctly?" review — either
before running, or when nothing is obviously failing yet.

## Checklist (report what is present, missing, or inconsistent)
1. **system/controlDict**
   - `application` matches the solver the user intends to run.
   - Time controls make sense: `startTime`, `endTime`, `deltaT`, and
     `writeControl` / `writeInterval`. For adjustable time stepping, check
     `adjustTimeStep`, `maxCo`, and `maxDeltaT`.
2. **system/fvSchemes** — `ddtSchemes`, `gradSchemes`, `divSchemes`,
   `laplacianSchemes`, and `snGradSchemes` are all present. A `steadyState` ddt
   scheme vs a transient one must match the solver.
3. **system/fvSolution** — there is a solver entry for every field being solved
   (`p`, `U`, and the turbulence fields), and the correct control block exists
   (`SIMPLE` for steady, `PIMPLE`/`PISO` for transient), including
   `relaxationFactors` and any `residualControl`.
4. **constant/** — `transportProperties` (e.g. `nu`) for incompressible flow,
   and turbulence settings (`turbulenceProperties`, or `momentumTransport` in
   newer versions) with a valid `simulationType`.
5. **0/** — a field file exists for every variable the solver and turbulence
   model require, each with correct `dimensions` and `boundaryField` patches.
6. **Cross-consistency** — "steady vs transient" must agree across controlDict
   (`application`), fvSchemes (`ddtSchemes`), and fvSolution (`SIMPLE` vs
   `PIMPLE`). The turbulence model in `constant/` must match the fields in `0/`.

## Guidance
- Present findings as a short checklist: what looks correct, what is missing, and
  what is inconsistent — naming the exact file for each item.
- If a file needed to verify an item was not provided, list it under "Missing
  information" instead of assuming it is fine.
