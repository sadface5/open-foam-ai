# Example: `broken_case`

A tiny, deliberately-flawed pitzDaily-style case for testing the assistant.
Point **📁 Select Case Folder** at this folder, pick **Solver Divergence Debugger**
(or **Boundary Condition Checker**), and click **Analyze**.

Planted issues you should see the tool react to:
- **Missing `0/omega`** — the turbulence model is `kOmegaSST`, which requires
  `omega`. The **deterministic checks** should flag this as CRITICAL *before*
  Claude even runs (watch the progress panel).
- **Aggressive `div(phi,U)` scheme** (`Gauss linear`) in `system/fvSchemes` — not
  deterministically flagged, but Claude should raise it as a startup-stability risk.

There is no mesh here (only `constant/polyMesh/boundary`), so mesh-quality items
should come back as "Unknown — requires running checkMesh", which is exactly the
honest behavior we want.
