const fs = require('fs');
let code = fs.readFileSync('frontend/app.js', 'utf8');

// Check if we need to auto-fill the form fields explicitly so the user sees them.
// Let's search where rfd3_contig is auto-filled for user.
const contigFillCode = `answers.rfd3_contig = inferredContig;`;
console.log(code.includes(contigFillCode));
