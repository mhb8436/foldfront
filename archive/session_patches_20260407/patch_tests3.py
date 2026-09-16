import re

with open("pipeline-mcp/src/pipeline_mcp/pipeline.py", "r") as f:
    code = f.read()

# Fix import error: `_has_nonpositive_resseq`
old = "from .bio.pdb import _has_nonpositive_resseq, _prepare_pdb_text_for_design_context"
# Oh wait, `_has_nonpositive_resseq` is defined in `pipeline.py`, not `bio.pdb.py`.
# Ah! Let's check where it is defined.
