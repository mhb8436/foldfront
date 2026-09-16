import sys

with open("frontend/app.js", "r") as f:
    content = f.read()

old_filter = """    .filter((q) => q.id !== "relax_score_per_residue_cutoff" || state.answers.relax_enabled === true)"""

new_filter = """    .filter((q) => q.id !== "relax_score_per_residue_cutoff" || state.answers.relax_enabled === true)
    .filter((q) => !["evolution_initial_samples", "evolution_rounds", "evolution_samples_per_round"].includes(q.id) || state.answers.evolution_mode === true)"""

content = content.replace(old_filter, new_filter)

with open("frontend/app.js", "w") as f:
    f.write(content)
