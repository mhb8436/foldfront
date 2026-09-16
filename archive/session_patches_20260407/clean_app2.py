import re

with open("frontend/app.js", "r") as f:
    lines = f.readlines()

# Remove lines 9833-9839 (or whatever the exact lines are)
# Let's find the exact indices
start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if "af2_plddt_cutoff: Number.parseFloat(el.evolutionAf2PlddtCutoffInput?.value || \"80\")," in line:
        start_idx = i
    if "fixed_positions_extra: String(el.evolutionFixedPositionsExtraInput?.value || \"\").trim()," in line:
        end_idx = i

if start_idx != -1 and end_idx != -1:
    del lines[start_idx:end_idx+1]
    with open("frontend/app.js", "w") as f:
        f.writelines(lines)
    print("Cleaned initEvolutionLauncher duplicates.")
else:
    print("Could not find duplicates.")
