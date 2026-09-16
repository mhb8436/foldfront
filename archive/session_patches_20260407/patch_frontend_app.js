const fs = require('fs');
let code = fs.readFileSync('frontend/app.js', 'utf8');

// We need to add the fields to the UI builder and state parsing
// We can define the fields in pipeline.js first, but actually app.js also maintains a list of valid fields somewhere or passes them.

// Find where answers are built
const targetAnswers = `function buildAnswerPayload(mode) {
  const answers = { ...state.answers };`;
// App just spreads state.answers. The fields are rendered by `buildFieldControl`.
