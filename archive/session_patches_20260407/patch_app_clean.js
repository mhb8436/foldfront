const fs = require('fs');
const path = '/opt/protein_pipeline/frontend/app.js';
let content = fs.readFileSync(path, 'utf8');

// 1. Add to ANSWER_BOOL_KEYS
content = content.replace(
  /"confirm_run",\s*\]\);/g,
  `"confirm_run",
  "evolution_mode",
]);`
);

// 2. Add to ANSWER_INT_KEYS
content = content.replace(
  /"conservation_cluster_kmer_per_seq",\s*\]\);/g,
  `"conservation_cluster_kmer_per_seq",
  "evolution_initial_samples",
  "evolution_rounds",
  "evolution_samples_per_round",
]);`
);

// 3. Add to choiceQuestionIds
content = content.replace(
  /"confirm_run",\s*\]\);\s*const isFileQuestion/g,
  `"confirm_run",
    "evolution_mode",
  ]);

  const isFileQuestion`
);

// 4. Add to compactChoiceQuestionIds
content = content.replace(
  /"mask_consensus_apply",\s*\]\);/g,
  `"mask_consensus_apply",
    "evolution_mode",
  ]);`
);

// 5. Add visibility logic
content = content.replace(
  /if \(id === "rfd3_use"\) \{/g,
  `if (
    id === "evolution_initial_samples" ||
    id === "evolution_rounds" ||
    id === "evolution_samples_per_round"
  ) {
    return answers.evolution_mode === true;
  }
  if (id === "rfd3_use") {`
);

// 6. Add questions to buildManualPlan
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

// Add to pipeline mode (after num_seq_per_tier)
content = content.replace(
  /id: "num_seq_per_tier",[\s\S]*?default: 2,\s*\},/g,
  match => match + '\n' + questionsToAdd
);

// Add to design mode (after num_seq_per_tier if it exists, or at the end of questions.push)
// Wait, design mode doesn't have num_seq_per_tier. Let's add it after bioemu_steering_config_text in design mode.
// We can use a more specific regex for design mode.
content = content.replace(
  /(if \(mode === "design"\) \{[\s\S]*?id: "bioemu_steering_config_text",\s*required: false,\s*\})\s*\);\s*\}/g,
  (match, p1) => p1 + ',\n' + questionsToAdd + '    );\n  }'
);

// 7. Add renderBooleanField for evolution_mode
const renderEvolutionMode = `
    renderBooleanField({
      id: "evolution_mode",
      fallback: false,
      onLabel: "On",
      offLabel: "Off",
      rerender: true,
    });
`;

content = content.replace(
  /renderBooleanField\(\{\s*id: "relax_enabled",[\s\S]*?\}\);/g,
  match => match + '\n' + renderEvolutionMode
);

fs.writeFileSync(path, content);
