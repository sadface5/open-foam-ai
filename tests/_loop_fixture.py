"""
A runnable OpenFOAM case used by the live autonomous-loop test.

It is the standard lid-driven cavity, but deliberately shipped WITHOUT a
generated mesh: it has a blockMeshDict and nothing in constant/polyMesh. The
loop is expected to notice the missing mesh, run blockMesh, and confirm the
problem is gone.

Kept in its own module (leading underscore) so the test runner does not mistake
it for a test file.
"""
import tempfile
from pathlib import Path

_HEADER = ("FoamFile\n{{\n    version 2.0;\n    format ascii;\n"
           "    class {cls};\n    object {obj};\n}}\n")

FILES = {
    "system/blockMeshDict": _HEADER.format(cls="dictionary", obj="blockMeshDict") + """
scale 0.1;
vertices
(
    (0 0 0) (1 0 0) (1 1 0) (0 1 0)
    (0 0 0.1) (1 0 0.1) (1 1 0.1) (0 1 0.1)
);
blocks ( hex (0 1 2 3 4 5 6 7) (20 20 1) simpleGrading (1 1 1) );
edges ();
boundary
(
    movingWall   { type wall;  faces ((3 7 6 2)); }
    fixedWalls   { type wall;  faces ((0 4 7 3) (2 6 5 1) (1 5 4 0)); }
    frontAndBack { type empty; faces ((0 3 2 1) (4 5 6 7)); }
);
mergePatchPairs ();
""",
    "system/controlDict": _HEADER.format(cls="dictionary", obj="controlDict") + """
application     icoFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         0.1;
deltaT          0.005;
writeControl    timeStep;
writeInterval   20;
""",
    "system/fvSchemes": _HEADER.format(cls="dictionary", obj="fvSchemes") + """
ddtSchemes       { default Euler; }
gradSchemes      { default Gauss linear; }
divSchemes       { default none; div(phi,U) Gauss upwind; }
laplacianSchemes { default Gauss linear orthogonal; }
snGradSchemes    { default orthogonal; }
""",
    "system/fvSolution": _HEADER.format(cls="dictionary", obj="fvSolution") + """
solvers
{
    p { solver PCG; preconditioner DIC; tolerance 1e-06; relTol 0.05; }
    U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-05; relTol 0; }
}
PISO { nCorrectors 2; nNonOrthogonalCorrectors 0; }
""",
    "constant/transportProperties":
        _HEADER.format(cls="dictionary", obj="transportProperties")
        + "\nnu              [0 2 -1 0 0 0 0] 0.01;\n",
    "0/U": _HEADER.format(cls="volVectorField", obj="U") + """
dimensions      [0 1 -1 0 0 0 0];
internalField   uniform (0 0 0);
boundaryField
{
    movingWall   { type fixedValue; value uniform (1 0 0); }
    fixedWalls   { type noSlip; }
    frontAndBack { type empty; }
}
""",
    "0/p": _HEADER.format(cls="volScalarField", obj="p") + """
dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;
boundaryField
{
    movingWall   { type zeroGradient; }
    fixedWalls   { type zeroGradient; }
    frontAndBack { type empty; }
}
""",
}


def build_cavity_case() -> Path:
    """
    Create the case in a temporary folder and return its path.

    It is placed under the user's home area because VM-backed Docker daemons
    (Docker Toolbox) can only mount folders the VM shares -- usually C:\\Users.
    """
    base = Path.home() / "AppData" / "Local" / "Temp"
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="foamloop_", dir=str(base)))
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root
