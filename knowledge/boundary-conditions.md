# Boundary conditions in the 0/ folder

Each file in the 0/ folder defines one field (U, p, k, epsilon, omega, nut, T,
and so on). Every file has a dimensions line, an internalField (the initial
value), and a boundaryField block with one entry per mesh patch.

Patch names in every 0/ file must exactly match the patch names in
constant/polyMesh/boundary. A misspelled or missing patch name is one of the most
common setup errors and usually stops the run immediately.

For incompressible solvers, pressure p is kinematic pressure, with dimensions
[0 2 -2 0 0 0 0] (units of m^2/s^2), not Pascals. Writing p in Pascals, or using
the wrong dimensions line, is a frequent mistake. For compressible solvers, p is
the real static pressure in Pascals with dimensions [1 -1 -2 0 0 0 0].

Velocity and pressure boundary conditions must form a consistent pair. A typical
velocity inlet uses fixedValue for U and zeroGradient for p. A typical pressure
outlet uses fixedValue for p and zeroGradient or inletOutlet for U. A no-slip wall
uses noSlip (or fixedValue (0 0 0)) for U and zeroGradient for p. Fixing both U
and p to fixedValue on the same patch over-constrains the problem.

Turbulence fields at walls normally use wall-function boundary conditions:
kqRWallFunction for k, epsilonWallFunction for epsilon, omegaWallFunction for
omega, and a nut wall function such as nutkWallFunction for nut. These must be
present on wall patches and be consistent with the turbulence model chosen in
constant/turbulenceProperties (or constant/momentumTransport).

inletOutlet is a useful boundary condition for outlets where flow might reverse:
it behaves like zeroGradient when flow leaves the domain and like a fixed
inletValue when flow tries to enter, which improves stability at outlets.
