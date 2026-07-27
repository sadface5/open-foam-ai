# fvSolution: linear solvers and relaxation

system/fvSolution defines how each field's linear system is solved and how the
outer algorithm is controlled. It has a "solvers" block (one entry per field) and
an algorithm block (SIMPLE, PIMPLE, or PISO).

For the pressure field p, the GAMG (geometric-algebraic multigrid) solver or PCG
(preconditioned conjugate gradient) are common choices. For velocity U and the
turbulence fields, smoothSolver or PBiCGStab are typical. Each solver entry sets a
tolerance and a relTol (relative tolerance) that control how tightly that field is
solved each iteration.

Under-relaxation factors live in the relaxationFactors sub-dictionary. They damp
how much a field changes per iteration in steady SIMPLE runs. Smaller factors are
more stable but converge more slowly. If a steady run diverges, lowering the p and
U relaxation factors is a common, easily reversible first step.

nNonOrthogonalCorrectors (inside the SIMPLE or PIMPLE block) adds extra pressure
correction passes to cope with non-orthogonal meshes. Raising it from 0 to 1 or 2
can help a run survive on a moderately non-orthogonal mesh, at some extra cost per
time step.

For transient PIMPLE runs, nCorrectors sets the number of PISO pressure
corrections per time step, and nOuterCorrectors sets the number of outer
(SIMPLE-style) loops per time step. residualControl can be used to stop outer
loops early once residuals are small enough.

Every field that the solver actually solves for must have an entry in the solvers
block. A missing entry (for example forgetting k or omega when using a turbulence
model) will cause the run to fail.
