const fs = require('fs');
let code = fs.readFileSync('frontend/lib/pipeline.js', 'utf8');

// Add the fields to WORKFLOW_STUDIO_STAGE_FIELDS -> msa, or a new pseudo-stage
// Let's just add them to msa for simplicity, or create an "evolution" stage if that breaks nothing.
// The easiest is just exposing them in the setup answers logic.
const oldFields = `  msa: Object.freeze(["target_input", "pdb_strip_nonpositive_resseq", "backbone_filter_use_dssp"]),`;
const newFields = `  msa: Object.freeze(["target_input", "pdb_strip_nonpositive_resseq", "backbone_filter_use_dssp", "evolution_mode", "evolution_initial_samples", "evolution_rounds", "evolution_samples_per_round"]),`;
code = code.replace(oldFields, newFields);

const oldAnswers = `    backbone_filter_use_dssp: true,`;
const newAnswers = `    backbone_filter_use_dssp: true,
    evolution_mode: false,
    evolution_initial_samples: 20,
    evolution_rounds: 3,
    evolution_samples_per_round: 5,`;
code = code.replace(oldAnswers, newAnswers);

fs.writeFileSync('frontend/lib/pipeline.js', code);
