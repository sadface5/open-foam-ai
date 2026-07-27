# Turbulence models and y+

The turbulence model is chosen in constant/turbulenceProperties (or, in newer
OpenFOAM versions, constant/momentumTransport). The simulationType is usually RAS
(Reynolds-averaged), LES, or laminar. For RAS, the RASModel keyword names the
model, for example kEpsilon or kOmegaSST, and turbulence is switched on with
"turbulence on;".

k-omega SST (kOmegaSST) is a robust, general-purpose RANS model. It performs well
in adverse pressure gradients and separated flow and works with both
wall-resolved and wall-functioned near-wall meshes. It is a good default when you
are unsure.

Standard k-epsilon (kEpsilon) is widely used for high-Reynolds internal and
free-shear flows. It typically relies on wall functions rather than resolving the
viscous sublayer, so it is less accurate for strongly separated flows.

y+ is the non-dimensional wall distance of the first cell centre. It determines
which near-wall treatment is valid. High-Reynolds wall functions are intended for
y+ roughly between 30 and 300. Wall-resolved (low-Reynolds) modelling instead
targets y+ around 1, which requires a much finer near-wall mesh.

If your mesh gives a y+ far outside the range your boundary conditions assume, the
near-wall solution will be inaccurate. Check y+ after a run (for example with the
yPlus post-processing utility) and adjust either the mesh (first-cell height) or
the wall treatment so they match.

The required fields depend on the model. k-epsilon needs k and epsilon (plus nut);
k-omega and k-omega SST need k and omega (plus nut). Every required field must
have a file in 0/ with appropriate boundary conditions, or the run will fail.
