"""
The Compiler (a.k.a. *Forge*) — *wish list → working ccFleet app*.

A staged, LLM-assisted pipeline with editable checkpoints (see ``systemPlan.md``):

    layer1.dream.md  --distill-->  layer2.params.yaml
                     --expand --> layer3.subparts/*.yaml
                     --build  --> config roots + domain/ + fenced labels  (verified)

Design contract:
  - **Spec is the source of truth** (D3/R5): edit ``system/`` and rebuild; generated
    files are disposable and tracked in ``.compiler-manifest.json``.
  - **Editable checkpoints** (R3): every stage output is a file you can read + correct.
  - **Safe rebuilds** (R7): re-run a *range* (``--from/--to``); approved artifacts are
    locked; editing an artifact marks downstream ``stale``.
  - **Verified builds** (R6): a build is "done" only when ``pytest`` + ``--mock`` boot
    + ``--dry-run`` pass (``compiler.gate``); otherwise it ships nothing.

This package is the meta-system; the generated app stays ≈ the template engine so
upstream improvements can be pulled (§10c).
"""

__version__ = "0.1.0"

# the pipeline stages, coarse → fine. Each stage's output is an editable artifact.
STAGES = ("dream", "params", "subparts", "app")

# the transforms between consecutive layers (what `--from X --to Y` runs)
TRANSFORMS = ("distill", "expand", "build")

# build.yaml stage status vocabulary
STATUS_APPROVED = "approved"   # you wrote/blessed it — never overwritten without --force
STATUS_DRAFT = "draft"         # Compiler-made
STATUS_STALE = "stale"         # an upstream artifact changed; re-run to refresh
