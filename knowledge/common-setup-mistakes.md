# Common OpenFOAM case setup mistakes

The application keyword in system/controlDict must name the solver you actually
run. If controlDict says one solver but you launch another, schemes, fields, and
algorithm blocks may not match, producing confusing errors.

Mixing steady and transient settings is a frequent inconsistency. A steady run
should use application simpleFoam, a steadyState ddtScheme in system/fvSchemes,
and a SIMPLE block in system/fvSolution. A transient run should use a transient
ddtScheme (such as Euler or backward) and a PIMPLE or PISO block. These three
places must agree.

Every solved field needs three things to line up: a file in 0/ with correct
boundary conditions, a solver entry in system/fvSolution, and (for turbulence
fields) a matching turbulence model in constant/. Forgetting any one of these is a
common cause of startup failures.

The dimensions line must be correct for each field. For incompressible solvers,
kinematic pressure p has dimensions [0 2 -2 0 0 0 0]. Kinematic viscosity nu in
constant/transportProperties has dimensions [0 2 -1 0 0 0 0]. A wrong dimensions
line is often reported as a dimension-mismatch error.

Time controls in controlDict should be sensible: endTime greater than startTime,
a deltaT appropriate for the flow, and a writeInterval that actually writes
results. A writeInterval that never triggers within the run leaves you with no
output to inspect.

Patch names must be identical everywhere: in constant/polyMesh/boundary and in the
boundaryField blocks of every 0/ file. Renaming a patch in the mesh without
updating the 0/ files, or a simple typo, will stop the run.
