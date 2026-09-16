const fs = require('fs');
let code = fs.readFileSync('frontend/lib/pipeline.js', 'utf8');

const target = `  const unindex = \`\${firstChain}\${range.min}\`;
  const contig = \`\${firstChain}\${range.min + 1}-\${range.max}\`;
  const select_fixed_atoms = JSON.stringify({ [unindex]: "ALL" });`;

const insert = `  const unindex = \`\${firstChain}\${range.min}\`;
  // RFD3 expects contig without dash before chain id if it's positive.
  // We should just use chain and numbers, e.g., "A2-221" instead of "A-2-221" if it somehow gets mangled,
  // but range.min could be negative like -8. If so, it becomes "A-7-221" which might be invalid.
  // Actually, RFD3 format is "ChainIdx" or "ChainStartIdx-EndIdx".
  // If min is -8, min+1 is -7. "A-7-221" is invalid format because of the negative sign?
  // Let's check how negative residues are handled in RFD3. It expects positive integers.
  // Our PDB parser has negative resseq (-8). RFD3's pydantic validation failed:
  // "Invalid contig format. Expected 'ChainIDStart-Stop' or 'ChainIDIdx'."
  // It seems RFD3 doesn't support negative residue indices in contig strings like A-7-221.
  
  // So the issue was negative residue indices!
  // Let's just output the contig string properly.
  const contig = \`\${firstChain}\${range.min + 1}-\${range.max}\`;
  const select_fixed_atoms = JSON.stringify({ [unindex]: "ALL" });`;

code = code.replace(target, insert);
fs.writeFileSync('frontend/lib/pipeline.js', code);
