const fs = require('fs');
let code = fs.readFileSync('frontend/app.js', 'utf8');

// I need to properly add the 4 fields to normalizeQuestions and defaultGroups if not there.
const t1 = `      type: "boolean",\n    },\n  };`;
const i1 = `      type: "boolean",\n    },\n    evolution_mode: {\n      type: "boolean",\n    },\n    evolution_initial_samples: {\n      type: "number",\n      default: 20,\n    },\n    evolution_rounds: {\n      type: "number",\n      default: 3,\n    },\n    evolution_samples_per_round: {\n      type: "number",\n      default: 5,\n    },\n  };`;

if (code.includes(t1)) {
    code = code.replace(t1, i1);
}

const t2 = `  const defaultGroups = {`;
const i2 = `  const defaultGroups = {\n    "Evolution (BO) Settings": ["evolution_mode", "evolution_initial_samples", "evolution_rounds", "evolution_samples_per_round"],`;

if (code.includes(t2)) {
    code = code.replace(t2, i2);
}

fs.writeFileSync('frontend/app.js', code);
