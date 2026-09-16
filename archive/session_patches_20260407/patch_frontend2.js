const fs = require('fs');
let code = fs.readFileSync('frontend/lib/pipeline.js', 'utf8');

code = code.replace(
  `function rfd3ModeUsesContig(mode) {
  const normalized = normalizeRfd3Mode(mode);
  return normalized === "legacy_contig" || normalized === "binder";
}`,
  `function rfd3ModeUsesContig(mode) {
  const normalized = normalizeRfd3Mode(mode);
  return normalized === "legacy_contig" || normalized === "binder" || normalized === "local_diversify" || normalized === "enzyme";
}`
);

code = code.replace(
  `function workflowStudioRfd3ModeUsesContig(mode) {
  const normalized = normalizeRfd3Mode(mode);
  return normalized === "legacy_contig" || normalized === "binder";
}`,
  `function workflowStudioRfd3ModeUsesContig(mode) {
  const normalized = normalizeRfd3Mode(mode);
  return normalized === "legacy_contig" || normalized === "binder" || normalized === "local_diversify" || normalized === "enzyme";
}`
);

fs.writeFileSync('frontend/lib/pipeline.js', code);
