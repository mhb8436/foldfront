import { artifactMetaFromPathForManifest, artifactMetaFromPath } from './frontend/lib/pipeline.js';
const path = "tiers/0.3/sample_0001/seq_001.pdb";
const meta = artifactMetaFromPath(path);
console.log("meta:", meta);
const manifest = {
  backbones: [
    { id: "sample_0001", source: "bioemu" }
  ]
};
const result = artifactMetaFromPathForManifest(path, manifest);
console.log("result:", result);
