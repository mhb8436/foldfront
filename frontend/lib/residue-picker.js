const DEFAULT_PROPERTY = Object.freeze({
  group: "unknown",
  color: "#9ca3af",
  label: "Unknown",
});

const PROPERTY_BY_AA = Object.freeze({
  A: { group: "hydrophobic", color: "#6d9773", label: "Hydrophobic" },
  V: { group: "hydrophobic", color: "#6d9773", label: "Hydrophobic" },
  I: { group: "hydrophobic", color: "#6d9773", label: "Hydrophobic" },
  L: { group: "hydrophobic", color: "#6d9773", label: "Hydrophobic" },
  M: { group: "hydrophobic", color: "#6d9773", label: "Hydrophobic" },
  F: { group: "hydrophobic", color: "#6d9773", label: "Hydrophobic" },
  P: { group: "hydrophobic", color: "#6d9773", label: "Hydrophobic" },
  G: { group: "special", color: "#8b5e3c", label: "Special" },
  C: { group: "special", color: "#8b5e3c", label: "Special" },
  U: { group: "special", color: "#8b5e3c", label: "Special" },
  S: { group: "polar", color: "#3a86a8", label: "Polar" },
  T: { group: "polar", color: "#3a86a8", label: "Polar" },
  N: { group: "polar", color: "#3a86a8", label: "Polar" },
  Q: { group: "polar", color: "#3a86a8", label: "Polar" },
  K: { group: "positive", color: "#be5a38", label: "Positive" },
  R: { group: "positive", color: "#be5a38", label: "Positive" },
  H: { group: "positive", color: "#be5a38", label: "Positive" },
  D: { group: "negative", color: "#355c7d", label: "Negative" },
  E: { group: "negative", color: "#355c7d", label: "Negative" },
  W: { group: "aromatic", color: "#c76d52", label: "Aromatic" },
  Y: { group: "aromatic", color: "#c76d52", label: "Aromatic" },
});

export const DEFAULT_SURFACE_AREA_CUTOFF = 2.5;
const DEFAULT_SURFACE_PROBE_RADIUS = 1.4;
const DEFAULT_SURFACE_POINT_COUNT = 96;
const DEFAULT_INTERFACE_DISTANCE = 8;
const PROTEIN_LIKE_HETATM_RESN = new Set([
  "ALA",
  "ARG",
  "ASN",
  "ASP",
  "CYS",
  "GLN",
  "GLU",
  "GLY",
  "HIS",
  "ILE",
  "LEU",
  "LYS",
  "MET",
  "MSE",
  "PHE",
  "PRO",
  "SER",
  "THR",
  "TRP",
  "TYR",
  "VAL",
]);
const VDW_RADIUS_BY_ELEMENT = Object.freeze({
  H: 1.2,
  C: 1.7,
  N: 1.55,
  O: 1.52,
  F: 1.47,
  P: 1.8,
  S: 1.8,
  CL: 1.75,
  BR: 1.85,
  I: 1.98,
  SE: 1.9,
});

function buildUnitSpherePoints(count = DEFAULT_SURFACE_POINT_COUNT) {
  const samples = Math.max(12, Math.trunc(Number(count) || DEFAULT_SURFACE_POINT_COUNT));
  const points = [];
  const offset = 2 / samples;
  const increment = Math.PI * (3 - Math.sqrt(5));
  for (let index = 0; index < samples; index += 1) {
    const y = index * offset - 1 + offset / 2;
    const radius = Math.sqrt(Math.max(0, 1 - y * y));
    const phi = index * increment;
    points.push([Math.cos(phi) * radius, y, Math.sin(phi) * radius]);
  }
  return points;
}

const UNIT_SPHERE_POINTS = Object.freeze(buildUnitSpherePoints());

function normalizeChain(chain) {
  const text = String(chain || "")
    .trim()
    .toUpperCase();
  return text || "_";
}

function pushResidue(map, chain, resi) {
  const chainId = normalizeChain(chain);
  const resiNum = Number(resi);
  if (!Number.isFinite(resiNum)) return;
  if (!Array.isArray(map[chainId])) map[chainId] = [];
  if (!map[chainId].includes(resiNum)) {
    map[chainId].push(resiNum);
    map[chainId].sort((left, right) => left - right);
  }
}

function normalizeSelectionMap(raw) {
  const out = {};
  if (!raw || typeof raw !== "object") return out;
  Object.entries(raw).forEach(([chain, values]) => {
    if (!Array.isArray(values)) return;
    values.forEach((value) => pushResidue(out, chain, value));
  });
  return out;
}

function selectionResidueCount(selectionMap) {
  return Object.values(normalizeSelectionMap(selectionMap)).reduce(
    (total, values) => total + (Array.isArray(values) ? values.length : 0),
    0
  );
}

export function aminoAcidPropertyInfo(aminoAcid) {
  const aa = String(aminoAcid || "")
    .trim()
    .toUpperCase();
  return {
    aa,
    ...(PROPERTY_BY_AA[aa] || DEFAULT_PROPERTY),
  };
}

export function sequenceResiduePalette(sequence = "") {
  return String(sequence || "")
    .trim()
    .split("")
    .map((aa, index) => ({
      index: index + 1,
      aa,
      ...aminoAcidPropertyInfo(aa),
    }));
}

function residueNumberForIndex(order = [], index = 0) {
  const residue = Number(order[index - 1]);
  return Number.isFinite(residue) ? residue : index;
}

export function buildSequenceSelectionTracks(sequenceByChain = {}, residueOrderByChain = {}, options = {}) {
  const lineLength = Math.max(1, Number(options.lineLength) || 24);
  const labelEvery = Math.max(1, Number(options.labelEvery) || 2);
  return Object.entries(sequenceByChain || {})
    .map(([chain, sequence]) => {
      const palette = sequenceResiduePalette(sequence);
      const order = Array.isArray(residueOrderByChain?.[chain]) ? residueOrderByChain[chain] : [];
      const cells = palette.map((entry) => ({
        ...entry,
        resi: residueNumberForIndex(order, entry.index),
      }));
      const rows = [];
      for (let offset = 0; offset < cells.length; offset += lineLength) {
        const rowCells = cells.slice(offset, offset + lineLength);
        if (!rowCells.length) continue;
        const labels = rowCells
          .filter((cell, idx) => idx === 0 || idx === rowCells.length - 1 || cell.index % labelEvery === 0)
          .map((cell, idx, arr) => ({
            slot: rowCells.findIndex((item) => item.index === cell.index),
            value: String(cell.resi),
            edge: idx === 0 || idx === arr.length - 1,
          }));
        rows.push({
          startIndex: rowCells[0].index,
          endIndex: rowCells[rowCells.length - 1].index,
          startResi: rowCells[0].resi,
          endResi: rowCells[rowCells.length - 1].resi,
          cells: rowCells,
          labels,
        });
      }
      return {
        chain: normalizeChain(chain),
        rows,
      };
    })
    .filter((track) => Array.isArray(track.rows) && track.rows.length);
}

export function mergeResidueSelectionMaps(baseMap = {}, addMap = {}) {
  const merged = normalizeSelectionMap(baseMap);
  Object.entries(normalizeSelectionMap(addMap)).forEach(([chain, values]) => {
    values.forEach((resi) => pushResidue(merged, chain, resi));
  });
  return merged;
}

export function selectionMapContains(baseMap = {}, subsetMap = {}) {
  const base = normalizeSelectionMap(baseMap);
  const subset = normalizeSelectionMap(subsetMap);
  let matched = 0;
  for (const [chain, values] of Object.entries(subset)) {
    if (!Array.isArray(values) || !values.length) continue;
    const existing = new Set(base[chain] || []);
    for (const resi of values) {
      matched += 1;
      if (!existing.has(resi)) return false;
    }
  }
  return matched > 0;
}

export function subtractResidueSelectionMaps(baseMap = {}, removeMap = {}) {
  const remaining = normalizeSelectionMap(baseMap);
  Object.entries(normalizeSelectionMap(removeMap)).forEach(([chain, values]) => {
    if (!Array.isArray(values) || !values.length) return;
    const next = new Set(remaining[chain] || []);
    values.forEach((resi) => {
      next.delete(resi);
    });
    if (next.size) {
      remaining[chain] = Array.from(next).sort((left, right) => left - right);
    } else {
      delete remaining[chain];
    }
  });
  return remaining;
}

export function toggleResidueSelectionMaps(baseMap = {}, toggleMap = {}) {
  return selectionMapContains(baseMap, toggleMap)
    ? subtractResidueSelectionMaps(baseMap, toggleMap)
    : mergeResidueSelectionMaps(baseMap, toggleMap);
}

export function resolveResidueSelectionMaps({
  activePresetIds = [],
  presetSelectionsById = {},
  manualSelection = {},
  excludedSelection = {},
} = {}) {
  let selection = {};
  const active = Array.isArray(activePresetIds) ? activePresetIds : [];
  active.forEach((presetId) => {
    const presetSelection = presetSelectionsById?.[String(presetId || "").trim()];
    if (!presetSelection || typeof presetSelection !== "object") return;
    selection = mergeResidueSelectionMaps(selection, presetSelection);
  });
  selection = mergeResidueSelectionMaps(selection, manualSelection);
  return subtractResidueSelectionMaps(selection, excludedSelection);
}

export function resolveResiduePickerSelectionState({
  selectionFallback = {},
  manualSelection = {},
  excludedSelection = {},
  activePresetIds = [],
  presetSelectionsById = {},
  allowFallback = false,
} = {}) {
  const nextExcludedSelection = normalizeSelectionMap(excludedSelection);
  const nextManualSelectionBase = normalizeSelectionMap(manualSelection);
  const nextActivePresetIds = (Array.isArray(activePresetIds) ? activePresetIds : [])
    .map((presetId) => String(presetId || "").trim())
    .filter((presetId, index, list) => presetId && list.indexOf(presetId) === index)
    .filter((presetId) => selectionResidueCount(presetSelectionsById?.[presetId] || {}) > 0);
  const explicitState =
    selectionResidueCount(nextManualSelectionBase) > 0 ||
    selectionResidueCount(nextExcludedSelection) > 0 ||
    nextActivePresetIds.length > 0;
  const nextManualSelection =
    explicitState || !allowFallback ? nextManualSelectionBase : normalizeSelectionMap(selectionFallback);
  return {
    manualSelection: nextManualSelection,
    excludedSelection: nextExcludedSelection,
    activePresetIds: nextActivePresetIds,
    selection: resolveResidueSelectionMaps({
      activePresetIds: nextActivePresetIds,
      presetSelectionsById,
      manualSelection: nextManualSelection,
      excludedSelection: nextExcludedSelection,
    }),
  };
}

export function clearResiduePickerSelectionState(state = {}) {
  const source = state && typeof state === "object" && !Array.isArray(state) ? state : {};
  return {
    ...source,
    selection: {},
    manualSelection: {},
    excludedSelection: {},
    activePresetIds: [],
    notice: "",
  };
}

export function buildDetachedResiduePickerStoragePayload(request = {}) {
  const source = request && typeof request === "object" && !Array.isArray(request) ? request : {};
  const snapshot =
    source.snapshot && typeof source.snapshot === "object" && !Array.isArray(source.snapshot) ? source.snapshot : {};
  return {
    ...source,
    targetPdbText: "",
    rfd3PdbText: "",
    snapshot: {
      ...snapshot,
      pdbText: "",
      sourceLabel: "",
      sourceKey: "",
    },
  };
}

export function buildDetachedResiduePickerResultStoragePayload(result = {}) {
  const source = result && typeof result === "object" && !Array.isArray(result) ? result : {};
  const snapshot =
    source.snapshot && typeof source.snapshot === "object" && !Array.isArray(source.snapshot) ? source.snapshot : {};
  const predicted =
    source.predictedResult && typeof source.predictedResult === "object" && !Array.isArray(source.predictedResult)
      ? source.predictedResult
      : null;
  return {
    ...source,
    snapshot: {
      ...snapshot,
      pdbText: "",
      sourceLabel: "",
      sourceKey: "",
    },
    predictedResult: predicted
      ? {
          ...predicted,
          selectedPdb: "",
        }
      : predicted,
  };
}

export function queryPositionsToResidueSelectionMap(fixedPositionsMap = {}, residueOrderByChain = {}) {
  const out = {};
  Object.entries(normalizeSelectionMap(fixedPositionsMap)).forEach(([chain, values]) => {
    const order = Array.isArray(residueOrderByChain?.[chain]) ? residueOrderByChain[chain] : [];
    if (!order.length) return;
    const mapped = values
      .map((queryPos) => order[Number(queryPos) - 1])
      .filter((resi) => Number.isFinite(resi) && resi > 0);
    if (!mapped.length) return;
    out[chain] = Array.from(new Set(mapped)).sort((left, right) => left - right);
  });
  return out;
}

export function normalizeSurfaceAreaCutoff(value, fallback = DEFAULT_SURFACE_AREA_CUTOFF) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

export function classifyResidueExposure(entries = [], options = {}) {
  const surfaceAreaCutoff = normalizeSurfaceAreaCutoff(options.surfaceAreaCutoff, DEFAULT_SURFACE_AREA_CUTOFF);
  const surfaceMaxNeighbors = Math.max(0, Number(options.surfaceMaxNeighbors) || 3);
  const coreMinNeighbors = Math.max(surfaceMaxNeighbors + 1, Number(options.coreMinNeighbors) || 8);
  const surface = {};
  const core = {};
  const interfaceResidues = {};
  (Array.isArray(entries) ? entries : []).forEach((entry) => {
    if (!entry || typeof entry !== "object") return;
    const chain = entry.chain;
    const resi = entry.resi;
    if (Boolean(entry.interface)) pushResidue(interfaceResidues, chain, resi);
    const exposedAreaMax = Number(entry.exposedAreaMax);
    const exposedAreaSum = Number(entry.exposedAreaSum);
    const hasAreaSignal = Number.isFinite(exposedAreaMax) || Number.isFinite(exposedAreaSum);
    if (hasAreaSignal) {
      const areaSignal = Number.isFinite(exposedAreaMax) ? exposedAreaMax : exposedAreaSum;
      if (areaSignal > surfaceAreaCutoff) {
        pushResidue(surface, chain, resi);
      } else {
        pushResidue(core, chain, resi);
      }
      return;
    }
    const neighborCount = Number(entry.neighborCount);
    if (Number.isFinite(neighborCount) && neighborCount <= surfaceMaxNeighbors) {
      pushResidue(surface, chain, resi);
    } else if (Number.isFinite(neighborCount) && neighborCount >= coreMinNeighbors) {
      pushResidue(core, chain, resi);
    }
  });
  return {
    surface,
    core,
    interface: interfaceResidues,
  };
}

function normalizedTierKey(tier) {
  const value = Number(tier);
  if (!Number.isFinite(value) || value <= 0) return "";
  return value > 1 ? String(Math.round(value)) : String(Math.round(value * 100));
}

export function conservedTierPresetState(conservationPreview, tier = 0.3, lang = "en") {
  const isKo = String(lang || "").trim().toLowerCase().startsWith("ko");
  const preview = conservationPreview && typeof conservationPreview === "object" ? conservationPreview : null;
  const tiers = preview && preview.tiers && typeof preview.tiers === "object" ? preview.tiers : null;
  const key = normalizedTierKey(tier);
  const enabled = Boolean(tiers && key && Array.isArray(tiers[key]) && tiers[key].length);
  return {
    enabled,
    reason: enabled
      ? ""
      : isKo
        ? "conservation preview가 아직 없어 conserved preset을 사용할 수 없습니다."
      : "Conservation preview is not available yet for conserved presets.",
  };
}

export function availableConservedTierPresetKeys(conservationPreview, tiers = [0.3, 0.5, 0.7]) {
  const preview = conservationPreview && typeof conservationPreview === "object" ? conservationPreview : null;
  const available = preview && preview.tiers && typeof preview.tiers === "object" ? preview.tiers : null;
  return (Array.isArray(tiers) ? tiers : [])
    .map((tier) => normalizedTierKey(tier))
    .filter((key) => key && Array.isArray(available?.[key]) && available[key].length);
}

function proteinAtomRecordIncluded(line = "") {
  const record = String(line.slice(0, 6) || "")
    .trim()
    .toUpperCase();
  if (record === "ATOM") return true;
  if (record !== "HETATM") return false;
  const resn = String(line.slice(17, 20) || "")
    .trim()
    .toUpperCase();
  return PROTEIN_LIKE_HETATM_RESN.has(resn);
}

function inferAtomElement(line = "", atomName = "") {
  const explicit = String(line.slice(76, 78) || "")
    .replace(/[^A-Za-z]/g, "")
    .toUpperCase();
  if (explicit) return explicit;
  const fallback = String(atomName || "")
    .replace(/[^A-Za-z]/g, "")
    .toUpperCase();
  if (!fallback) return "";
  return fallback[0];
}

function vdwRadiusForElement(element = "") {
  const normalized = String(element || "")
    .trim()
    .toUpperCase();
  return VDW_RADIUS_BY_ELEMENT[normalized] || 1.7;
}

function parseProteinAtoms(pdbText = "") {
  return String(pdbText || "")
    .split(/\r?\n/)
    .map((line) => String(line || ""))
    .filter((line) => proteinAtomRecordIncluded(line))
    .map((line) => {
      const atomName = line.slice(12, 16).trim();
      const altLoc = line.slice(16, 17).trim();
      if (altLoc && altLoc !== "A" && altLoc !== "1") return null;
      const chain = normalizeChain(line.slice(21, 22));
      const resi = Number.parseInt(line.slice(22, 26).trim(), 10);
      const resn = line.slice(17, 20).trim();
      const x = Number(line.slice(30, 38).trim());
      const y = Number(line.slice(38, 46).trim());
      const z = Number(line.slice(46, 54).trim());
      const element = inferAtomElement(line, atomName);
      if (
        !Number.isFinite(resi) ||
        !Number.isFinite(x) ||
        !Number.isFinite(y) ||
        !Number.isFinite(z) ||
        !element ||
        element === "H"
      ) {
        return null;
      }
      return {
        chain,
        resi,
        resn,
        atomName,
        element,
        radius: vdwRadiusForElement(element),
        x,
        y,
        z,
        residueKey: `${chain}:${resi}`,
      };
    })
    .filter(Boolean);
}

function buildAtomGrid(atoms = [], cellSize = 1) {
  const grid = new Map();
  (Array.isArray(atoms) ? atoms : []).forEach((atom, index) => {
    const cellX = Math.floor(Number(atom?.x || 0) / cellSize);
    const cellY = Math.floor(Number(atom?.y || 0) / cellSize);
    const cellZ = Math.floor(Number(atom?.z || 0) / cellSize);
    const key = `${cellX}:${cellY}:${cellZ}`;
    if (!grid.has(key)) grid.set(key, []);
    grid.get(key).push(index);
  });
  return grid;
}

function estimateAtomExposedAreas(atoms = [], options = {}) {
  if (!Array.isArray(atoms) || !atoms.length) return [];
  const probeRadius = Math.max(0, Number(options.probeRadius) || DEFAULT_SURFACE_PROBE_RADIUS);
  const samplePoints =
    Array.isArray(options.samplePoints) && options.samplePoints.length ? options.samplePoints : UNIT_SPHERE_POINTS;
  const prepared = atoms.map((atom) => ({
    ...atom,
    surfaceRadius: Number(atom.radius) + probeRadius,
  }));
  const maxSurfaceRadius = prepared.reduce(
    (maxRadius, atom) => Math.max(maxRadius, Number(atom?.surfaceRadius || 0)),
    0
  );
  const cellSize = Math.max(4, Number(options.cellSize) || maxSurfaceRadius * 2 + probeRadius);
  const grid = buildAtomGrid(prepared, cellSize);

  return prepared.map((atom, atomIndex) => {
    const atomCellX = Math.floor(Number(atom.x || 0) / cellSize);
    const atomCellY = Math.floor(Number(atom.y || 0) / cellSize);
    const atomCellZ = Math.floor(Number(atom.z || 0) / cellSize);
    const searchRadius = Math.max(1, Math.ceil((atom.surfaceRadius + maxSurfaceRadius) / cellSize));
    const neighborCandidates = new Set();
    for (let dx = -searchRadius; dx <= searchRadius; dx += 1) {
      for (let dy = -searchRadius; dy <= searchRadius; dy += 1) {
        for (let dz = -searchRadius; dz <= searchRadius; dz += 1) {
          const key = `${atomCellX + dx}:${atomCellY + dy}:${atomCellZ + dz}`;
          (grid.get(key) || []).forEach((candidateIndex) => {
            if (candidateIndex !== atomIndex) neighborCandidates.add(candidateIndex);
          });
        }
      }
    }
    const neighbors = Array.from(neighborCandidates)
      .map((candidateIndex) => prepared[candidateIndex])
      .filter((neighbor) => {
        const dx = Number(atom.x || 0) - Number(neighbor?.x || 0);
        const dy = Number(atom.y || 0) - Number(neighbor?.y || 0);
        const dz = Number(atom.z || 0) - Number(neighbor?.z || 0);
        const centerDistanceSq = dx * dx + dy * dy + dz * dz;
        const interactionRadius = Number(atom.surfaceRadius || 0) + Number(neighbor?.surfaceRadius || 0);
        return centerDistanceSq <= interactionRadius * interactionRadius;
      });

    let accessiblePointCount = 0;
    samplePoints.forEach(([ux, uy, uz]) => {
      const pointX = Number(atom.x || 0) + ux * Number(atom.surfaceRadius || 0);
      const pointY = Number(atom.y || 0) + uy * Number(atom.surfaceRadius || 0);
      const pointZ = Number(atom.z || 0) + uz * Number(atom.surfaceRadius || 0);
      const blocked = neighbors.some((neighbor) => {
        const dx = pointX - Number(neighbor?.x || 0);
        const dy = pointY - Number(neighbor?.y || 0);
        const dz = pointZ - Number(neighbor?.z || 0);
        const neighborRadius = Number(neighbor?.surfaceRadius || 0);
        return dx * dx + dy * dy + dz * dz < neighborRadius * neighborRadius - 1e-6;
      });
      if (!blocked) accessiblePointCount += 1;
    });

    const surfaceRadius = Number(atom.surfaceRadius || 0);
    return {
      ...atom,
      exposedArea:
        (accessiblePointCount / samplePoints.length) * 4 * Math.PI * surfaceRadius * surfaceRadius,
    };
  });
}

function residueCentroid(coords = [], ca = null) {
  if (Array.isArray(ca) && ca.length === 3) return ca;
  const points = Array.isArray(coords) && coords.length ? coords : [[0, 0, 0]];
  return points.reduce(
    (acc, point) => [
      acc[0] + Number(point?.[0] || 0) / points.length,
      acc[1] + Number(point?.[1] || 0) / points.length,
      acc[2] + Number(point?.[2] || 0) / points.length,
    ],
    [0, 0, 0]
  );
}

function buildResidueExposureEntries(atoms = []) {
  const residueMap = new Map();
  (Array.isArray(atoms) ? atoms : []).forEach((atom) => {
    const key = String(atom?.residueKey || `${normalizeChain(atom?.chain)}:${atom?.resi}`);
    const entry = residueMap.get(key) || {
      chain: normalizeChain(atom?.chain),
      resi: Number(atom?.resi),
      resn: String(atom?.resn || "").trim(),
      coords: [],
      ca: null,
      exposedAreaMax: 0,
      exposedAreaSum: 0,
    };
    const coord = [Number(atom?.x || 0), Number(atom?.y || 0), Number(atom?.z || 0)];
    entry.coords.push(coord);
    if (String(atom?.atomName || "").trim().toUpperCase() === "CA") {
      entry.ca = coord;
    }
    const exposedArea = Number(atom?.exposedArea);
    if (Number.isFinite(exposedArea)) {
      entry.exposedAreaMax = Math.max(entry.exposedAreaMax, exposedArea);
      entry.exposedAreaSum += exposedArea;
    }
    residueMap.set(key, entry);
  });
  return Array.from(residueMap.values()).map((entry) => ({
    chain: entry.chain,
    resi: entry.resi,
    resn: entry.resn,
    centroid: residueCentroid(entry.coords, entry.ca),
    exposedAreaMax: entry.exposedAreaMax,
    exposedAreaSum: entry.exposedAreaSum,
  }));
}

function distance3(left, right) {
  return Math.hypot(
    Number(left?.[0] || 0) - Number(right?.[0] || 0),
    Number(left?.[1] || 0) - Number(right?.[1] || 0),
    Number(left?.[2] || 0) - Number(right?.[2] || 0)
  );
}

export function deriveResidueSpatialPresets(pdbText = "", options = {}) {
  const atoms = estimateAtomExposedAreas(parseProteinAtoms(pdbText), options);
  const residues = buildResidueExposureEntries(atoms);
  const interfaceDistance = Math.max(3, Number(options.interfaceDistance) || DEFAULT_INTERFACE_DISTANCE);
  const exposure = residues.map((entry, idx) => {
    let interfaceHit = false;
    residues.forEach((other, otherIdx) => {
      if (idx === otherIdx) return;
      const dist = distance3(entry.centroid, other.centroid);
      if (entry.chain !== other.chain && dist <= interfaceDistance) interfaceHit = true;
    });
    return {
      chain: entry.chain,
      resi: entry.resi,
      resn: entry.resn,
      centroid: entry.centroid,
      exposedAreaMax: entry.exposedAreaMax,
      exposedAreaSum: entry.exposedAreaSum,
      interface: interfaceHit,
    };
  });
  return {
    residues: exposure,
    ...classifyResidueExposure(exposure, options),
  };
}
