# Courant number and solver divergence

The Courant number (Co) measures how far information travels across a cell in one
time step: Co = U * deltaT / cellSize. For transient solvers using PISO or PIMPLE,
keeping the maximum Courant number at or below about 1 is a common stability
guideline. A max Courant number that is very large, or that grows every time
step, is a classic warning sign that the time step is too big.

To reduce the Courant number you can lower deltaT in system/controlDict. If the
case uses automatic time stepping (adjustTimeStep yes;), instead lower the maxCo
setting, for example from 1 to 0.5. Both changes are small and easy to reverse.

A rising Initial residual for the pressure field p, across successive time steps
or outer iterations, is the most common signature of divergence. The pressure
equation is usually the first to show trouble because it is the stiffest part of
an incompressible solve.

"bounding k", "bounding epsilon", and "bounding omega" messages mean a turbulence
quantity went negative and OpenFOAM clipped it back to a small positive floor.
This is normally a symptom rather than the root cause: the underlying trigger is
often too large a time step, poor mesh cells, or inconsistent boundary conditions.

A "Floating point exception" or "sigFpe" message, or values printed as nan or inf,
means the solution has blown up numerically rather than stopped cleanly. When you
see this, look upward in the log for the first sign of trouble (rising residuals,
a spiking Courant number, or bounding messages) to find where it started.

For steady SIMPLE runs, divergence is often controlled with under-relaxation.
Lowering the relaxation factors in system/fvSolution (for example the factors for
p and U) makes each step more conservative and can stabilise a run; you can raise
them again once residuals are decreasing steadily.
