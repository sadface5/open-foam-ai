# Reading checkMesh output

checkMesh is the OpenFOAM utility that reports mesh quality. Run it in the case
folder; it prints geometry statistics and then a list of checks that either pass
or fail. The summary line "Mesh OK." or "Failed N mesh checks" tells you the
overall result at a glance.

Non-orthogonality measures the angle between the line joining two cell centres and
the face normal between them. checkMesh reports a max and an average. A maximum
above roughly 70 degrees produces warnings and hurts both accuracy and stability.
Meshes with high non-orthogonality often need extra nNonOrthogonalCorrectors and a
limited snGradScheme.

Skewness measures how far a face's intersection with the cell-centre line is from
the face centre. Values above about 4 are flagged by checkMesh. High skewness
degrades accuracy and can destabilise a run.

Aspect ratio is the ratio of a cell's longest to shortest dimension. Very large
aspect ratios (thousands and above) are common in boundary-layer meshes but can
slow convergence and, in extreme cases, cause instability.

Negative cell volumes or negative face areas are fatal errors, not warnings. They
mean the mesh is geometrically invalid (cells are inside-out or tangled). Such a
mesh must be regenerated or repaired; no numerical setting can safely work around
it.

Numerical tolerance for a moderately poor mesh comes from two settings:
nNonOrthogonalCorrectors in system/fvSolution, and a limited surface-normal
gradient scheme such as "limited corrected 0.33" for snGradSchemes in
system/fvSchemes. These help a run survive a slightly bad mesh but do not fix the
mesh itself.
