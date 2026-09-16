const fs = require('fs');
const path = '/opt/protein_pipeline/frontend/lib/pipeline.js';
let content = fs.readFileSync(path, 'utf8');

content = content.replace(
  /if \(baseStage !== "rfd3"\) return fields;/g,
  `if (baseStage === "design") {
    return fields.filter((fieldId) => {
      if (
        fieldId === "evolution_initial_samples" ||
        fieldId === "evolution_rounds" ||
        fieldId === "evolution_samples_per_round"
      ) {
        return answers.evolution_mode === true;
      }
      return true;
    });
  }
  if (baseStage !== "rfd3") return fields;`
);

fs.writeFileSync(path, content);
