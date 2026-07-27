# Choosing an OpenFOAM solver

The first question is steady versus transient. Steady-state incompressible turbulent
flow is usually solved with simpleFoam, which uses the SIMPLE algorithm and a
steadyState ddt scheme. Time-accurate incompressible flow uses a PIMPLE or PISO based
solver such as pimpleFoam or pisoFoam with a transient ddt scheme (Euler or backward).

icoFoam is a transient solver for laminar, incompressible, Newtonian flow with no
turbulence model. It is a good teaching solver for simple cases like the lid-driven
cavity, but it should not be used when turbulence matters.

simpleFoam is the standard steady-state incompressible solver with RANS turbulence.
Use it when you only need the converged mean flow (for example drag on a body in
steady conditions) and do not need time history.

pimpleFoam is a transient incompressible turbulent solver built on the PIMPLE
algorithm (a merged PISO-SIMPLE method). Its outer correctors allow larger time steps
and Courant numbers than PISO, so it is a good default for transient RANS or for
unsteady flows that would be slow with a strict Courant limit.

pisoFoam is a transient incompressible turbulent solver using the PISO algorithm. It
is accurate but generally needs the maximum Courant number kept below about 1, which
can mean small time steps.

For compressible flow, use a density-based/pressure-based compressible solver. Common
choices are rhoSimpleFoam for steady compressible flow and rhoPimpleFoam for transient
compressible flow. Flows with significant density or temperature variation, or Mach
numbers above roughly 0.3, generally need a compressible solver rather than an
incompressible one.

For two-phase free-surface flows (for example a dam break or a sloshing tank),
interFoam solves two incompressible, immiscible fluids using the volume-of-fluid (VOF)
method with a phase-fraction field alpha. Related solvers include multiphaseInterFoam
for more than two phases and interIsoFoam for sharper interface capturing.

For buoyancy-driven heat transfer (natural convection), buoyantSimpleFoam handles
steady cases and buoyantPimpleFoam handles transient cases. These solve the energy
equation and include gravity, and they typically use p_rgh rather than p.

For conjugate heat transfer between solids and fluids, chtMultiRegionFoam solves
coupled regions with different physics in each region.

potentialFoam is not a full flow solver; it computes a potential-flow velocity field
and is often used to initialize a case so the first iterations of the real solver are
more stable.

Match the required fields to the solver and turbulence model. An incompressible RANS
solver needs U and p plus the turbulence fields for the chosen model (k and epsilon
for k-epsilon; k and omega for k-omega and k-omega SST), plus nut. A compressible or
heat-transfer solver additionally needs a temperature or energy field and the
thermophysical properties. Missing any required field will stop the run.

Changing the solver is a large change. It usually requires editing the application
entry in system/controlDict, adjusting the ddt scheme in fvSchemes and the algorithm
block in fvSolution (SIMPLE versus PIMPLE/PISO), and providing the fields the new
solver expects. Prefer smaller fixes first, and only switch solvers when the current
choice is clearly mismatched to the physics.
