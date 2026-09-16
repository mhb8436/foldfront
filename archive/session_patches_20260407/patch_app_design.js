const fs = require('fs');
const path = '/opt/protein_pipeline/frontend/app.js';
let content = fs.readFileSync(path, 'utf8');

const questionsToAdd = `      {
        id: "evolution_mode",
        labelKey: "question.evolutionMode.label",
        questionKey: "question.evolutionMode.help",
        label: "Evolution Mode",
        question: "Enable Bayesian Optimization for sequence design.",
        required: false,
        default: false,
      },
      {
        id: "evolution_initial_samples",
        labelKey: "question.evolutionInitialSamples.label",
        questionKey: "question.evolutionInitialSamples.help",
        label: "Evolution Initial Samples",
        question: "Number of initial samples for BO.",
        required: false,
        default: 20,
      },
      {
        id: "evolution_rounds",
        labelKey: "question.evolutionRounds.label",
        questionKey: "question.evolutionRounds.help",
        label: "Evolution Rounds",
        question: "Number of BO rounds.",
        required: false,
        default: 3,
      },
      {
        id: "evolution_samples_per_round",
        labelKey: "question.evolutionSamplesPerRound.label",
        questionKey: "question.evolutionSamplesPerRound.help",
        label: "Evolution Samples Per Round",
        question: "Number of samples per BO round.",
        required: false,
        default: 5,
      },
`;

content = content.replace(
  /id: "bioemu_steering_config_text",[\s\S]*?\}\s*\);\s*\}/g,
  match => match.replace(/\)\s*;\s*\}/, ',\n' + questionsToAdd + '    );\n  }')
);

fs.writeFileSync(path, content);
