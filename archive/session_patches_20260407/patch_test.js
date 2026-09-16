const fs = require('fs');
const path = '/opt/protein_pipeline/frontend/tests/pipeline.test.js';
let content = fs.readFileSync(path, 'utf8');

content = content.replace(
  /'ligand_mask_use_original_target'\s*\]/g,
  `'ligand_mask_use_original_target',
    'evolution_mode',
    'evolution_initial_samples',
    'evolution_rounds',
    'evolution_samples_per_round'
  ]`
);

fs.writeFileSync(path, content);
