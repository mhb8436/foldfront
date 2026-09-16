import { inferRfd3LocalDiversifyEnzymeDefaults, inferredRfd3ContigRanges } from './frontend/lib/pipeline.js';
const payload = {
  rfd3_input_pdb: `ATOM      1  N   GLY A  -8      15.443  64.536   2.287  1.00 35.19           N  
ATOM      2  CA  GLY A  -8      15.288  64.229   0.775  1.00 32.77           C  
ATOM      3  C   GLY A  -8      16.594  63.869   0.090  1.00 44.91           C  
ATOM      4  O   GLY A  -8      16.601  63.375  -1.055  1.00 41.48           O  
ATOM      5  N   HIS A  -7      17.716  64.084   0.778  1.00 37.73           N  
ATOM      6  CA  HIS A  -7      17.288  64.229   0.775  1.00 32.77           C  
ATOM      7  C   HIS A  -7      18.594  63.869   0.090  1.00 44.91           C  
ATOM      8  O   HIS A  -7      18.601  63.375  -1.055  1.00 41.48           O  
ATOM      9  N   VAL A  -6      19.716  64.084   0.778  1.00 37.73           N  
ATOM     10  CA  VAL A  -6      19.288  64.229   0.775  1.00 32.77           C  
ATOM     11  C   VAL A  -6      20.594  63.869   0.090  1.00 44.91           C  
ATOM     12  O   VAL A  -6      20.601  63.375  -1.055  1.00 41.48           O  `
};
console.log("Ranges:", inferredRfd3ContigRanges(payload));
console.log("Defaults:", inferRfd3LocalDiversifyEnzymeDefaults(payload));
