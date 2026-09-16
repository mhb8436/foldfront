const fs = require('fs');
let appCode = fs.readFileSync('frontend/app.js', 'utf8');
let pipelineCode = fs.readFileSync('frontend/lib/pipeline.js', 'utf8');

// I will add the UI element later, this is just to confirm I can patch it
