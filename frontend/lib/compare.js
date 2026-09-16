import { formatConservationTierLabel, formatConservationTierValue } from "./pipeline.js";

function normalizeChainId(value) {
  const text = String(value || "")
    .trim()
    .toUpperCase();
  return text || "_";
}

function formatLegendMetric(value, digits = 2) {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits) : "-";
}

function finiteMetricNumber(value) {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function hasPositiveMetricCount(value) {
  const num = finiteMetricNumber(value);
  return num !== null && num > 0;
}

function relaxDistributionHasMetrics(metric) {
  if (!metric || typeof metric !== "object") return false;
  return (
    hasPositiveMetricCount(metric.count) ||
    finiteMetricNumber(metric.p10) !== null ||
    finiteMetricNumber(metric.p25) !== null ||
    finiteMetricNumber(metric.median) !== null ||
    finiteMetricNumber(metric.p75) !== null ||
    finiteMetricNumber(metric.p90) !== null ||
    finiteMetricNumber(metric.iqr) !== null
  );
}

function relaxBucketHasMetrics(bucket) {
  if (!bucket || typeof bucket !== "object") return false;
  return (
    hasPositiveMetricCount(bucket.relax_candidate_total) ||
    hasPositiveMetricCount(bucket.relax_selected_total) ||
    finiteMetricNumber(bucket.relax_median) !== null
  );
}

function normalizeTierLabel(value) {
  const text = formatConservationTierValue(value);
  return text === "-" ? "" : text;
}

function compareRoleKey(meta) {
  return String(meta?.compareRole || "")
    .trim()
    .toLowerCase();
}

function compareSourceLabel(source, lang = "en") {
  const normalized = String(source || "")
    .trim()
    .toLowerCase();
  if (normalized === "rfd3") return "RFD3";
  if (normalized === "bioemu") return "BioEmu";
  if (normalized === "wt") return lang === "ko" ? "WT" : "WT";
  return normalized || (lang === "ko" ? "기타" : "other");
}

function compareProviderLabel(provider = "colabfold") {
  return /af2/i.test(String(provider || "")) ? "AlphaFold2" : "ColabFold";
}

const BASE_ANALYZE_CHART_VIEW_OPTIONS = Object.freeze([
  Object.freeze({ id: "plddt_rmsd", labelKey: "analyze.chart.option.plddtRmsd" }),
  Object.freeze({ id: "plddt_soluprot", labelKey: "analyze.chart.option.plddtSoluprot" }),
  Object.freeze({ id: "score_hist", labelKey: "analyze.chart.option.scoreHist" }),
  Object.freeze({ id: "tier_pass", labelKey: "analyze.chart.option.tierPass" }),
]);

const RELAX_ANALYZE_CHART_VIEW_OPTIONS = Object.freeze([
  Object.freeze({ id: "plddt_relax", labelKey: "analyze.chart.option.plddtRelax" }),
  Object.freeze({ id: "rmsd_relax", labelKey: "analyze.chart.option.rmsdRelax" }),
]);

export function comparisonSummaryHasRelaxMetrics(summary = {}) {
  const wtRelax =
    summary?.wt_vs_design && typeof summary.wt_vs_design === "object" && summary.wt_vs_design.relax
      ? summary.wt_vs_design.relax
      : null;
  if (
    wtRelax &&
    typeof wtRelax === "object" &&
    (finiteMetricNumber(wtRelax.wt) !== null ||
      finiteMetricNumber(wtRelax.design_median) !== null ||
      finiteMetricNumber(wtRelax.delta_design_minus_wt) !== null ||
      hasPositiveMetricCount(wtRelax.design_total) ||
      hasPositiveMetricCount(wtRelax.design_selected))
  ) {
    return true;
  }

  const source = summary?.source_compare && typeof summary.source_compare === "object" ? summary.source_compare : {};
  if (Object.values(source).some((bucket) => relaxBucketHasMetrics(bucket))) {
    return true;
  }

  const funnelOverall =
    summary?.funnel && typeof summary.funnel === "object" && summary.funnel.overall ? summary.funnel.overall : null;
  if (relaxBucketHasMetrics(funnelOverall)) {
    return true;
  }

  const tierRows = Array.isArray(summary?.tier_compare) ? summary.tier_compare : [];
  if (tierRows.some((row) => relaxBucketHasMetrics(row))) {
    return true;
  }

  const relaxDistribution =
    summary?.distributions && typeof summary.distributions === "object" ? summary.distributions.relax : null;
  return relaxDistributionHasMetrics(relaxDistribution);
}

export function runCompareHasRelaxMetrics(result = {}) {
  return ["current", "baseline", "delta"].some((key) => {
    const bucket = result?.[key];
    return bucket && typeof bucket === "object" && finiteMetricNumber(bucket.relax_median) !== null;
  });
}

export function hitListHasRelaxMetrics(rows = []) {
  return (Array.isArray(rows) ? rows : []).some((row) => finiteMetricNumber(row?.relax) !== null);
}

function hitListHasMetricPair(rows = [], xKey = "", yKey = "") {
  return (Array.isArray(rows) ? rows : []).some(
    (row) => finiteMetricNumber(row?.[xKey]) !== null && finiteMetricNumber(row?.[yKey]) !== null
  );
}

export function hitListRelaxColumnEnabled(rows = [], result = {}) {
  return hitListHasRelaxMetrics(rows) || result?.relax_enabled === true;
}

export function analyzeChartViewOptions({ rows = [], summary = {} } = {}) {
  const options = [BASE_ANALYZE_CHART_VIEW_OPTIONS[0]];
  if (hitListHasMetricPair(rows, "plddt", "soluprot")) {
    options.push(BASE_ANALYZE_CHART_VIEW_OPTIONS[1]);
  }
  if (hitListHasRelaxMetrics(rows) || comparisonSummaryHasRelaxMetrics(summary)) {
    options.push(...RELAX_ANALYZE_CHART_VIEW_OPTIONS);
  }
  options.push(...BASE_ANALYZE_CHART_VIEW_OPTIONS.slice(2));
  return options.map((option) => ({ ...option }));
}

export function buildStructureDiffLegend({ rmsd = null, p90Distance = null, commonCount = null, lang = "en" } = {}) {
  const isKo = String(lang || "").trim().toLowerCase().startsWith("ko");
  const prefix = isKo
    ? "구조 차이: 황색 1.5-3.0A, 적색 >3.0A"
    : "Structure delta: amber 1.5-3.0A, red >3.0A";
  return `${prefix} · RMSD=${formatLegendMetric(rmsd)}A, P90=${formatLegendMetric(p90Distance)}A, n=${Number(
    commonCount || 0
  )}`;
}

export function buildCompareScopeDescription({ leftMeta = null, rightMeta = null, provider = "colabfold", lang = "en" } = {}) {
  const isKo = String(lang || "").trim().toLowerCase().startsWith("ko");
  const providerLabel = compareProviderLabel(provider);
  const lines = [];
  const leftRole = compareRoleKey(leftMeta);
  const rightRole = compareRoleKey(rightMeta);

  if (leftRole === "wt_colabfold") {
    lines.push(
      isKo
        ? `WT ${providerLabel}은 야생형 서열에서 예측한 기준 구조입니다. 실험 입력 구조가 아니라 비교 기준선으로 사용합니다.`
        : `WT ${providerLabel} is the predicted wild-type reference structure. It is a comparison baseline, not the experimental input structure.`
    );
  } else if (leftRole === "input_reference") {
    lines.push(
      isKo
        ? "입력 reference는 사용자가 제공한 원본 구조입니다."
        : "The input reference is the original user-provided structure."
    );
  }

  if (rightRole === "af2_candidate") {
    const tierText = formatConservationTierLabel(rightMeta?.tier, lang);
    const sourceText = compareSourceLabel(rightMeta?.backboneSource || rightMeta?.source, lang);
    lines.push(
      isKo
        ? `${sourceText}${tierText ? ` ${tierText}` : ""} 후보는 전체 집계가 아니라 현재 선택된 단일 candidate입니다.`
        : `${sourceText}${tierText ? ` ${tierText}` : ""} candidate is a single candidate, not the whole aggregate.`
    );
  } else if (rightRole === "backbone_snapshot") {
    lines.push(
      isKo
        ? "백본 snapshot은 design/AF2 이전 단계의 생성 구조입니다."
        : "A backbone snapshot is a generated structure before downstream design/AF2 filtering."
    );
  }

  if (!lines.length) {
    lines.push(
      isKo
        ? "좌측은 기준 구조, 우측은 현재 선택된 비교 대상입니다."
        : "The left side is the reference structure and the right side is the currently selected comparison target."
    );
  }
  return lines.join(" ");
}

export function buildCompareMetaTooltip(fieldKey = "", { provider = "colabfold", lang = "en" } = {}) {
  const isKo = String(lang || "").trim().toLowerCase().startsWith("ko");
  const providerLabel = compareProviderLabel(provider);
  const key = String(fieldKey || "").trim();
  if (isKo) {
    const ko = {
      role: "이 구조가 비교에서 어떤 역할인지 보여줍니다. 예: 입력 기준, WT 기준선, AF2 후보.",
      source: "이 구조를 만든 소스입니다. 예: RFD3, BioEmu, WT 기준선.",
      provenance: "어느 단계와 산출 경로에서 온 구조인지 설명합니다.",
      tier: "이 구조가 속한 서열 보존율 구간입니다. 보통 보존율이 높을수록 제약이 더 강합니다.",
      backbone: "downstream design/AF2에 연결된 backbone 식별자입니다.",
      chains: "현재 비교에 포함된 체인 요약입니다.",
      fixedCount: "ProteinMPNN 설계에서 고정된 잔기 개수입니다.",
      wtDiff: "WT 서열 대비 바뀐 위치 수와 비교 길이, identity를 함께 보여줍니다.",
      inputReferenceRmsd: "사용자가 올린 입력 구조와 현재 구조 사이의 C-alpha RMSD입니다.",
      workingBackboneRmsd: "현재 run의 작업 백본 target.pdb와 현재 구조 사이의 C-alpha RMSD입니다.",
      wtStructRmsd: `WT ${providerLabel} 기준 구조와 현재 구조 사이의 C-alpha RMSD입니다.`,
      commonCa: "RMSD 계산에 실제로 공통 정렬된 C-alpha 잔기 수입니다.",
      predScope: `${providerLabel} 지표가 이 파일 자체인지, WT 기준인지, tier/backbone 요약인지 구분합니다.`,
      predSelected: `${providerLabel} 결과 중 현재 scope에서 최종 선택된 개수입니다.`,
      predPlddt: `${providerLabel}의 구조 confidence 지표입니다. 일반적으로 높을수록 좋습니다.`,
      predRmsd: `${providerLabel} 결과에 연결된 구조 차이 RMSD입니다.`,
      path: "현재 비교 항목으로 선택된 실제 artifact 파일 경로입니다.",
    };
    return (
      ko[key] ||
      `${providerLabel} 기반 비교 컨텍스트 항목입니다. hover/focus 시 간단한 설명을 확인할 수 있습니다.`
    );
  }
  const en = {
    role: "Shows how this structure is used in the comparison, such as input reference, WT baseline, or AF2 candidate.",
    source: "Identifies which source produced this structure, such as RFD3, BioEmu, or the WT baseline.",
    provenance: "Explains which stage and artifact path this structure came from.",
    tier: "Sequence-conservation band associated with this structure. Higher conservation usually means stricter constraints.",
    backbone: "Backbone identifier propagated into downstream design and AF2 steps.",
    chains: "Summary of chains currently present in this comparison item.",
    fixedCount: "Number of residues kept fixed during ProteinMPNN design.",
    wtDiff: "Sequence difference versus wild type, reported as changed positions, compared length, and identity.",
    inputReferenceRmsd: "C-alpha RMSD between this structure and the original user-provided input structure.",
    workingBackboneRmsd: "C-alpha RMSD between this structure and the current run working-backbone target.pdb.",
    wtStructRmsd: `C-alpha RMSD between this structure and the WT ${providerLabel} baseline.`,
    commonCa: "Number of aligned common C-alpha residues used for RMSD.",
    predScope: `${providerLabel} scope tells you whether the metric belongs to this exact file, the WT reference, or a tier/backbone summary.`,
    predSelected: `Number of ${providerLabel} results retained in the current scope.`,
    predPlddt: `${providerLabel} confidence score for the predicted structure. Higher is usually better.`,
    predRmsd: `RMSD value associated with the relevant ${providerLabel} scope.`,
    path: "Actual artifact file path used for this comparison row.",
  };
  return (
    en[key] ||
    `${providerLabel}-based compare metadata field. Hover or focus to read a short explanation.`
  );
}

export function compareResidueMetricPosition(left, right) {
  const leftChain = normalizeChainId(left?.chain);
  const rightChain = normalizeChainId(right?.chain);
  if (leftChain !== rightChain) return leftChain.localeCompare(rightChain);

  const leftResi = Number(left?.resi);
  const rightResi = Number(right?.resi);
  if (Number.isFinite(leftResi) && Number.isFinite(rightResi) && leftResi !== rightResi) {
    return leftResi - rightResi;
  }
  if (Number.isFinite(leftResi) && !Number.isFinite(rightResi)) return -1;
  if (!Number.isFinite(leftResi) && Number.isFinite(rightResi)) return 1;
  return String(left?.key || "").localeCompare(String(right?.key || ""));
}

export function normalizeDesignChains(values) {
  if (!Array.isArray(values)) return [];
  const out = [];
  const seen = new Set();
  values.forEach((value) => {
    const chain = normalizeChainId(value);
    if (chain === "_" || seen.has(chain)) return;
    seen.add(chain);
    out.push(chain);
  });
  return out;
}

export function coerceFiniteMetricValue(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" && !value.trim()) return null;
  const num = typeof value === "number" ? value : Number(value);
  return Number.isFinite(num) ? num : null;
}

export function extractDesignChainsFromPayload(payload) {
  if (!payload || typeof payload !== "object") return [];
  const candidates = [
    payload.design_chains_used,
    payload.auto_selected_design_chains,
    payload.design_chains,
    payload.requested_design_chains,
  ];
  for (const candidate of candidates) {
    const chains = normalizeDesignChains(candidate);
    if (chains.length) return chains;
  }
  return [];
}

const PDB_CHAIN_RECORD_RE = /^(ATOM  |HETATM|ANISOU|TER   )/;

export function filterPdbTextByChains(pdbText, chains) {
  const allowedChains = normalizeDesignChains(chains);
  const source = String(pdbText || "");
  if (!source.trim() || !allowedChains.length) return source;
  const allowed = new Set(allowedChains.map((chain) => normalizeChainId(chain)));
  const hasTrailingNewline = /\r?\n$/.test(source);
  const filtered = source
    .split(/\r?\n/)
    .filter((line) => {
      if (!PDB_CHAIN_RECORD_RE.test(line)) return true;
      return allowed.has(normalizeChainId(line.slice(21, 22)));
    })
    .join("\n");
  return hasTrailingNewline ? `${filtered}\n` : filtered;
}

export function selectResidueStripMetrics(metrics, options = {}) {
  const items = Array.isArray(metrics)
    ? metrics.filter((item) => item && typeof item === "object")
    : [];
  if (!items.length) return [];

  const midThreshold = Number(options.midThreshold);
  const threshold = Number.isFinite(midThreshold) ? midThreshold : 1.5;
  const maxCount = Math.max(1, Number(options.maxCount) || 220);
  const fallbackCount = Math.max(1, Number(options.fallbackCount) || 60);
  const significant = items.filter((item) => Number(item.distance || 0) > threshold);
  const source = significant.length ? significant.slice(0, maxCount) : items.slice(0, fallbackCount);
  return source.slice().sort(compareResidueMetricPosition);
}
