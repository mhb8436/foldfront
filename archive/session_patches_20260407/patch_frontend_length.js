const fs = require('fs');
let code = fs.readFileSync('frontend/lib/pipeline.js', 'utf8');

const targetFunc = `function inferredRfd3ContigRanges(payload = {}) {
  const rfd3Input = String(payload?.rfd3_input_pdb || "").trim();
  const targetPdb = String(payload?.target_pdb || "").trim();
  const targetInput = String(payload?.target_input || "").trim();
  const pdbText =
    rfd3Input || targetPdb || (targetInput && detectTargetKey(targetInput) === "target_pdb" ? targetInput : "");
  if (!pdbText) return null;
  const ranges = {};
  String(pdbText)
    .split(/\\r?\\n/)
    .forEach((line) => {
      if (!isProteinPdbAtomLine(line)) return;
      const chainId = normalizeRfd3ChainId(line[21] || "");
      const resSeq = Number.parseInt(line.slice(22, 26).trim(), 10);
      if (!Number.isFinite(resSeq)) return;
      const entry = ranges[chainId] || { minPos: null, maxPos: null };
      if (resSeq > 0) {
        entry.minPos = entry.minPos === null ? resSeq : Math.min(entry.minPos, resSeq);
        entry.maxPos = entry.maxPos === null ? resSeq : Math.max(entry.maxPos, resSeq);
      }
      ranges[chainId] = entry;
    });
  const normalized = Object.entries(ranges).reduce((acc, [chainId, entry]) => {
    if (entry.minPos === null || entry.maxPos === null) return acc;
    acc[chainId] = { min: entry.minPos, max: entry.maxPos };
    return acc;
  }, {});
  return Object.keys(normalized).length ? normalized : null;
}`;

const replacementFunc = `function inferredRfd3ContigRanges(payload = {}) {
  const rfd3Input = String(payload?.rfd3_input_pdb || "").trim();
  const targetPdb = String(payload?.target_pdb || "").trim();
  const targetInput = String(payload?.target_input || "").trim();
  const pdbText =
    rfd3Input || targetPdb || (targetInput && detectTargetKey(targetInput) === "target_pdb" ? targetInput : "");
  if (!pdbText) return null;
  
  const uniqueResidues = {};
  
  String(pdbText)
    .split(/\\r?\\n/)
    .forEach((line) => {
      if (!isProteinPdbAtomLine(line)) return;
      const chainId = normalizeRfd3ChainId(line[21] || "");
      const resSeq = Number.parseInt(line.slice(22, 26).trim(), 10);
      const iCode = line.slice(26, 27).trim();
      if (!Number.isFinite(resSeq)) return;
      
      const residueKey = \`\${resSeq}_\${iCode}\`;
      if (!uniqueResidues[chainId]) {
        uniqueResidues[chainId] = new Set();
      }
      uniqueResidues[chainId].add(residueKey);
    });
    
  const normalized = Object.entries(uniqueResidues).reduce((acc, [chainId, resSet]) => {
    const size = resSet.size;
    if (size === 0) return acc;
    // Backend renumbers starting from 1
    acc[chainId] = { min: 1, max: size };
    return acc;
  }, {});
  
  return Object.keys(normalized).length ? normalized : null;
}`;

if (code.includes('if (resSeq > 0) {\\n        entry.minPos = entry.minPos === null ? resSeq : Math.min(entry.minPos, resSeq);')) {
  code = code.replace(targetFunc, replacementFunc);
  fs.writeFileSync('frontend/lib/pipeline.js', code);
  console.log("Successfully patched inferredRfd3ContigRanges!");
} else {
  console.log("Target function not found or already modified. Let's try regex.");
  // Fallback if formatting differs
  const startIdx = code.indexOf('function inferredRfd3ContigRanges(payload = {}) {');
  if (startIdx !== -1) {
    const endIdx = code.indexOf('}', code.indexOf('return Object.keys(normalized).length ? normalized : null;')) + 1;
    if (endIdx > startIdx) {
      code = code.slice(0, startIdx) + replacementFunc + code.slice(endIdx);
      fs.writeFileSync('frontend/lib/pipeline.js', code);
      console.log("Successfully patched using index replacement!");
    } else {
      console.log("Could not find end of function.");
    }
  } else {
    console.log("Could not find start of function.");
  }
}
