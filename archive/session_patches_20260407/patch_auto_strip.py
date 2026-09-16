import re
with open("pipeline-mcp/src/pipeline_mcp/pipeline.py", "r") as f:
    code = f.read()

# Current logic:
# if any(_has_nonpositive_resseq(text) for text in candidates):
#     effective_strip_nonpositive = True
#     auto_strip_nonpositive = True

# Change it to renumber_resseq_from_1:
old_t = """                if any(_has_nonpositive_resseq(text) for text in candidates):
                    effective_strip_nonpositive = True
                    auto_strip_nonpositive = True"""

new_t = """                if any(_has_nonpositive_resseq(text) for text in candidates):
                    effective_strip_nonpositive = False
                    effective_renumber = True
                    auto_strip_nonpositive = True"""

code = code.replace(old_t, new_t)

with open("pipeline-mcp/src/pipeline_mcp/pipeline.py", "w") as f:
    f.write(code)
