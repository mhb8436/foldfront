const fs = require('fs');
const path = '/opt/protein_pipeline/frontend/lib/pipeline.js';
let content = fs.readFileSync(path, 'utf8');

content = content.replace(
  /"ligand_mask_use_original_target",\s*\]\),/g,
  `"ligand_mask_use_original_target",
    "evolution_mode",
    "evolution_initial_samples",
    "evolution_rounds",
    "evolution_samples_per_round",
  ]),`
);

content = content.replace(
  /design: Object\.freeze\(\{\s*num_seq_per_tier: 2,\s*\}\),/g,
  `design: Object.freeze({
    num_seq_per_tier: 2,
    evolution_mode: false,
    evolution_initial_samples: 20,
    evolution_rounds: 3,
    evolution_samples_per_round: 5,
  }),`
);

fs.writeFileSync(path, content);
