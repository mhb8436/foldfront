import sys

with open("frontend/app.js", "r") as f:
    content = f.read()

old_set = """  const compactParameterQuestionIds = new Set([
    "compare_rmsd_scope",
    "bioemu_max_return_structures",
    "bioemu_num_samples",
    "rfd3_max_return_designs",
    "num_seq_per_tier",
    "af2_max_candidates_per_tier",
    "af2_plddt_cutoff",
    "af2_rmsd_cutoff",
    "relax_score_per_residue_cutoff",
  ]);"""

new_set = """  const compactParameterQuestionIds = new Set([
    "compare_rmsd_scope",
    "bioemu_max_return_structures",
    "bioemu_num_samples",
    "rfd3_max_return_designs",
    "num_seq_per_tier",
    "af2_max_candidates_per_tier",
    "af2_plddt_cutoff",
    "af2_rmsd_cutoff",
    "relax_score_per_residue_cutoff",
    "evolution_initial_samples",
    "evolution_rounds",
    "evolution_samples_per_round",
  ]);"""

old_priority = """  const compactParameterPriority = {
    compare_rmsd_scope: 5,
    bioemu_max_return_structures: 10,
    bioemu_num_samples: 20,
    rfd3_max_return_designs: 30,
    num_seq_per_tier: 40,
    af2_max_candidates_per_tier: 50,
    af2_plddt_cutoff: 60,
    af2_rmsd_cutoff: 70,
  };"""

new_priority = """  const compactParameterPriority = {
    compare_rmsd_scope: 5,
    bioemu_max_return_structures: 10,
    bioemu_num_samples: 20,
    rfd3_max_return_designs: 30,
    num_seq_per_tier: 40,
    af2_max_candidates_per_tier: 50,
    af2_plddt_cutoff: 60,
    af2_rmsd_cutoff: 70,
    evolution_initial_samples: 80,
    evolution_rounds: 81,
    evolution_samples_per_round: 82,
  };"""

content = content.replace(old_set, new_set)
content = content.replace(old_priority, new_priority)

with open("frontend/app.js", "w") as f:
    f.write(content)
