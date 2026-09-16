import {
  buildBillingCsv,
  buildBillingHistoryRows,
  buildBillingSeries,
  buildChartScaleLabels,
  buildEndpointScopeOptions,
  buildEndpointPatch,
  buildFleetBillingSeries,
  buildFleetUsageSeries,
  buildSparseMonitoringTickLabels,
  buildSpendChartSeries,
  buildMonitoringWindow,
  buildPreviousMonitoringWindow,
  buildUsageChartSeries,
  buildUsageCsv,
  buildWindowDownloadSuffix,
  canNavigateToNextMonitoringWindow,
  deriveEndpointStatus,
  formatCurrency,
  formatDateTime,
  formatManagedServices,
  formatMonitoringTimestamp,
  formatMonitoringWindowLabel,
  formatSpendSummary,
  normalizeApiBase,
  prepareEndpointForm,
  resolveDefaultApiBase,
  shiftMonitoringWindow,
  shouldScrollDetailIntoView,
  summarizeMonitoringComparison,
  summarizeFleet,
} from "./lib.js";

const STORAGE = {
  apiBase: "kbf.runpodAdmin.apiBase",
  token: "kbf.runpodAdmin.token",
  periodPreset: "kbf.runpodAdmin.periodPreset",
};

const state = {
  apiBase: normalizeApiBase(localStorage.getItem(STORAGE.apiBase)) || resolveDefaultApiBase(),
  token: localStorage.getItem(STORAGE.token) || "",
  user: null,
  fleet: { endpoints: [], missing_endpoints: [], summary: {}, managed_services: [] },
  billing: { records: [], summary: { total_cost: 0, by_endpoint: [] }, window: {} },
  detail: null,
  usageHistory: {},
  billingHistory: {},
  comparisonUsageHistory: {},
  comparisonBillingHistory: {},
  historyMeta: { collector: {}, window: {} },
  selectedEndpointId: "",
  loading: false,
  refreshTimer: null,
  periodPreset: String(localStorage.getItem(STORAGE.periodPreset) || "week").trim() || "week",
  monitoringWindow: buildMonitoringWindow(String(localStorage.getItem(STORAGE.periodPreset) || "week").trim() || "week"),
  comparisonWindow: buildPreviousMonitoringWindow(buildMonitoringWindow(String(localStorage.getItem(STORAGE.periodPreset) || "week").trim() || "week")),
  comparisonSummary: null,
};

const el = {
  apiBaseInput: document.getElementById("apiBaseInput"),
  homeBtn: document.getElementById("homeBtn"),
  saveApiBaseBtn: document.getElementById("saveApiBaseBtn"),
  pingApiBtn: document.getElementById("pingApiBtn"),
  refreshBtn: document.getElementById("refreshBtn"),
  logoutBtn: document.getElementById("logoutBtn"),
  sessionBadge: document.getElementById("sessionBadge"),
  lastRefreshValue: document.getElementById("lastRefreshValue"),
  banner: document.getElementById("banner"),
  loginCard: document.getElementById("loginCard"),
  loginUsername: document.getElementById("loginUsername"),
  loginPassword: document.getElementById("loginPassword"),
  loginBtn: document.getElementById("loginBtn"),
  loginBypassBtn: document.getElementById("loginBypassBtn"),
  appShell: document.getElementById("appShell"),
  managedOnlyToggle: document.getElementById("managedOnlyToggle"),
  includeWorkersToggle: document.getElementById("includeWorkersToggle"),
  periodPresetSelect: document.getElementById("periodPresetSelect"),
  periodNavPrevBtn: document.getElementById("periodNavPrevBtn"),
  periodNavNextBtn: document.getElementById("periodNavNextBtn"),
  periodLabel: document.getElementById("periodLabel"),
  serviceBoard: document.getElementById("serviceBoard"),
  endpointScopeSelector: document.getElementById("endpointScopeSelector"),
  endpointStageTitle: document.getElementById("endpointStageTitle"),
  endpointStageDescription: document.getElementById("endpointStageDescription"),
  missingEndpoints: document.getElementById("missingEndpoints"),
  endpointList: document.getElementById("endpointList"),
  detailTitle: document.getElementById("detailTitle"),
  detailPanel: document.querySelector(".detail-panel"),
  detailHero: document.getElementById("detailHero"),
  detailMeta: document.getElementById("detailMeta"),
  quickWarmBtn: document.getElementById("quickWarmBtn"),
  quickBurstBtn: document.getElementById("quickBurstBtn"),
  quickPauseBtn: document.getElementById("quickPauseBtn"),
  saveConfigBtn: document.getElementById("saveConfigBtn"),
  resetConfigBtn: document.getElementById("resetConfigBtn"),
  workerSummary: document.getElementById("workerSummary"),
  workerRows: document.getElementById("workerRows"),
  billingRows: document.getElementById("billingRows"),
  billingSummaryCards: document.getElementById("billingSummaryCards"),
  detailDownloads: document.getElementById("detailDownloads"),
  detailCharts: document.getElementById("detailCharts"),
  summaryEndpoints: document.getElementById("summaryEndpoints"),
  summaryWorkers: document.getElementById("summaryWorkers"),
  summaryLiveWorkers: document.getElementById("summaryLiveWorkers"),
  summaryLoad: document.getElementById("summaryLoad"),
  summaryCost: document.getElementById("summaryCost"),
  fieldName: document.getElementById("fieldName"),
  fieldGpuTypeIds: document.getElementById("fieldGpuTypeIds"),
  fieldDataCenterIds: document.getElementById("fieldDataCenterIds"),
  fieldTemplateId: document.getElementById("fieldTemplateId"),
  fieldNetworkVolumeId: document.getElementById("fieldNetworkVolumeId"),
  fieldScalerType: document.getElementById("fieldScalerType"),
  fieldScalerValue: document.getElementById("fieldScalerValue"),
  fieldWorkersMin: document.getElementById("fieldWorkersMin"),
  fieldWorkersMax: document.getElementById("fieldWorkersMax"),
  fieldIdleTimeout: document.getElementById("fieldIdleTimeout"),
  fieldExecutionTimeoutMs: document.getElementById("fieldExecutionTimeoutMs"),
  fieldFlashBoot: document.getElementById("fieldFlashBoot"),
};

function authHeaders() {
  return state.token ? { Authorization: `Bearer ${state.token}` } : {};
}

function mainConsolePath() {
  return window.location.pathname.startsWith("/pipeline") ? "/pipeline/" : "/";
}

function setBanner(message, tone = "info") {
  if (!message) {
    el.banner.className = "banner hidden";
    el.banner.textContent = "";
    return;
  }
  el.banner.className = `banner ${tone}`;
  el.banner.textContent = message;
}

function setLoading(loading) {
  state.loading = loading;
  const readOnly = Boolean(state.fleet?.read_only || state.detail?.read_only);
  el.refreshBtn.disabled = loading;
  el.loginBtn.disabled = loading;
  if (el.periodNavPrevBtn) el.periodNavPrevBtn.disabled = loading;
  if (el.periodNavNextBtn) el.periodNavNextBtn.disabled = loading || !canNavigateToNextMonitoringWindow(currentMonitoringWindow());
  el.saveConfigBtn.disabled = loading || !state.detail || readOnly;
  el.resetConfigBtn.disabled = loading || !state.detail || readOnly;
  el.quickWarmBtn.disabled = loading || !state.detail || readOnly;
  el.quickBurstBtn.disabled = loading || !state.detail || readOnly;
  el.quickPauseBtn.disabled = loading || !state.detail || readOnly;
}

function setSessionBadge() {
  if (state.user?.username) {
    const role = String(state.user.role || "user");
    el.sessionBadge.textContent = `${state.user.username} · ${role}`;
    el.logoutBtn.classList.remove("hidden");
    return;
  }
  el.sessionBadge.textContent = state.token ? "Authenticated" : "Anonymous";
  el.logoutBtn.classList.add("hidden");
}

function showApp(visible) {
  el.appShell.classList.toggle("hidden", !visible);
  el.loginCard.classList.toggle("hidden", visible);
}

function persistApiBase() {
  state.apiBase = normalizeApiBase(el.apiBaseInput.value) || resolveDefaultApiBase();
  el.apiBaseInput.value = state.apiBase;
  localStorage.setItem(STORAGE.apiBase, state.apiBase);
}

function persistPeriodPreset() {
  state.periodPreset = String(el.periodPresetSelect?.value || "week").trim() || "week";
  state.monitoringWindow = buildMonitoringWindow(state.periodPreset);
  state.comparisonWindow = buildPreviousMonitoringWindow(state.monitoringWindow);
  localStorage.setItem(STORAGE.periodPreset, state.periodPreset);
}

function persistToken(token) {
  state.token = token || "";
  if (state.token) {
    localStorage.setItem(STORAGE.token, state.token);
  } else {
    localStorage.removeItem(STORAGE.token);
  }
  setSessionBadge();
}

function normalizeUsageHistoryMap(raw, payload = {}) {
  if (!raw || typeof raw !== "object") return {};
  const historyMap = Array.isArray(raw)
    ? { [String(payload?.endpoint?.id || "").trim()]: raw }
    : raw;

  return Object.fromEntries(
    Object.entries(historyMap)
      .filter(([endpointId, samples]) => endpointId && Array.isArray(samples))
      .map(([endpointId, samples]) => [
        endpointId,
        samples
          .filter((sample) => sample && typeof sample === "object")
          .map((sample) => ({
            t: String(sample.t || sample.captured_at || ""),
            workers: Number(sample.workers || 0),
            queued: Number(sample.queued || 0),
            running: Number(sample.running || 0),
            completed: Number(sample.completed || 0),
            failed: Number(sample.failed || 0),
            retried: Number(sample.retried || 0),
            mode: String(sample.mode || "rest"),
          }))
          .filter((sample) => sample.t),
      ])
  );
}

function applyUsageHistory(payload, { replace = false } = {}) {
  if (!payload || typeof payload !== "object") return;
  const normalized = normalizeUsageHistoryMap(payload.usage_history, payload);
  if (!Object.keys(normalized).length) return;
  state.usageHistory = replace ? normalized : { ...state.usageHistory, ...normalized };
}

function applyBillingHistory(payload) {
  if (!payload || typeof payload !== "object") return;
  const raw = payload.billing_history;
  if (!raw || typeof raw !== "object") return;
  state.billingHistory = Object.fromEntries(
    Object.entries(raw)
      .filter(([endpointId, samples]) => endpointId && Array.isArray(samples))
      .map(([endpointId, samples]) => [
        endpointId,
        samples
          .filter((sample) => sample && typeof sample === "object")
          .map((sample) => ({
            t: String(sample.t || sample.bucket_start || ""),
            cost: Number(sample.cost || 0),
            records: Number(sample.records || 0),
            mode: String(sample.mode || "rest"),
          }))
          .filter((sample) => sample.t),
      ])
  );
}

function normalizeBillingHistoryMap(raw) {
  if (!raw || typeof raw !== "object") return {};
  return Object.fromEntries(
    Object.entries(raw)
      .filter(([endpointId, samples]) => endpointId && Array.isArray(samples))
      .map(([endpointId, samples]) => [
        endpointId,
        samples
          .filter((sample) => sample && typeof sample === "object")
          .map((sample) => ({
            t: String(sample.t || sample.bucket_start || ""),
            cost: Number(sample.cost || 0),
            records: Number(sample.records || 0),
            mode: String(sample.mode || "rest"),
          }))
          .filter((sample) => sample.t),
      ])
  );
}

function applyHistoryPayload(payload) {
  if (!payload || typeof payload !== "object") return;
  applyUsageHistory(payload, { replace: true });
  applyBillingHistory(payload);
  state.historyMeta = {
    collector: payload.collector && typeof payload.collector === "object" ? payload.collector : {},
    window: payload.window && typeof payload.window === "object" ? payload.window : {},
    source: String(payload.history_source || "server"),
  };
}

function endpointHistory(endpointId) {
  const history = state.usageHistory?.[String(endpointId || "").trim()];
  return Array.isArray(history) ? history : [];
}

function endpointBillingSeries(endpointId) {
  const endpointKey = String(endpointId || "").trim();
  const stored = state.billingHistory?.[endpointKey];
  if (Array.isArray(stored) && stored.length) {
    return stored;
  }
  return buildBillingSeries(state.billing?.records || [], endpointId, currentMonitoringWindow().preset === "months_6" ? 12 : 31);
}

function endpointBillingSummary(endpointId) {
  const targetId = String(endpointId || "").trim();
  const rows = Array.isArray(state.billing?.summary?.by_endpoint) ? state.billing.summary.by_endpoint : [];
  return rows.find((row) => String(row?.endpoint_id || "").trim() === targetId) || null;
}

function visibleEndpointIds() {
  return (Array.isArray(state.fleet?.endpoints) ? state.fleet.endpoints : [])
    .map((item) => String(item?.id || "").trim())
    .filter(Boolean);
}

function endpointNameMap() {
  return Object.fromEntries(
    (Array.isArray(state.fleet?.endpoints) ? state.fleet.endpoints : [])
      .map((endpoint) => [String(endpoint?.id || "").trim(), String(endpoint?.name || "").trim()])
      .filter(([endpointId]) => endpointId)
  );
}

function billingRowsForVisibleEndpoints() {
  const visibleIds = new Set(visibleEndpointIds());
  const rows = Array.isArray(state.billing?.summary?.by_endpoint) ? state.billing.summary.by_endpoint : [];
  if (!visibleIds.size) return rows;
  return rows.filter((row) => visibleIds.has(String(row?.endpoint_id || "").trim()));
}

function billingHistoryRows(endpointId = "") {
  const targetId = String(endpointId || "").trim();
  const endpointIds = targetId ? [targetId] : visibleEndpointIds();
  return buildBillingHistoryRows(state.billingHistory, endpointIds, endpointNameMap());
}

function currentMonitoringWindow() {
  return state.monitoringWindow || buildMonitoringWindow(state.periodPreset || "week");
}

function previousMonitoringWindow() {
  return state.comparisonWindow || buildPreviousMonitoringWindow(currentMonitoringWindow());
}

function monitoringRangeLabel() {
  return formatMonitoringWindowLabel(currentMonitoringWindow());
}

function monitoringDownloadSuffix() {
  return buildWindowDownloadSuffix(currentMonitoringWindow());
}

function comparisonRangeLabel() {
  return formatMonitoringWindowLabel(previousMonitoringWindow());
}

function comparisonUsageSeries() {
  return buildFleetUsageSeries(
    state.comparisonUsageHistory,
    visibleEndpointIds(),
    currentMonitoringWindow().preset === "months_6" ? 12 : 31
  );
}

function comparisonBillingSeries() {
  return buildFleetBillingSeries(
    state.comparisonBillingHistory,
    visibleEndpointIds(),
    currentMonitoringWindow().preset === "months_6" ? 12 : 31
  );
}

function computeComparisonSummary() {
  return summarizeMonitoringComparison({
    currentUsage: fleetUsageSeries(),
    previousUsage: comparisonUsageSeries(),
    currentBilling: fleetBillingSeries(),
    previousBilling: comparisonBillingSeries(),
  });
}

function formatDelta(metric = {}) {
  const delta = Number(metric?.delta || 0);
  const deltaPct = Number(metric?.deltaPct || 0);
  const sign = delta > 0 ? "+" : "";
  const tone = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
  return {
    text: `${sign}${deltaPct.toFixed(0)}%`,
    detail: `${sign}${delta.toFixed(2)}`,
    tone,
  };
}

function downloadTextFile(filename, text, mimeType = "text/plain;charset=utf-8") {
  const blob = new Blob([text], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function downloadChartSvg(containerId, filename) {
  const container = document.getElementById(containerId);
  const svg = container?.querySelector("svg");
  if (!svg) {
    setBanner("The selected chart has no SVG content to download yet.", "warn");
    return;
  }
  let markup = svg.outerHTML;
  if (!markup.includes("xmlns=")) {
    markup = markup.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ');
  }
  downloadTextFile(filename, markup, "image/svg+xml;charset=utf-8");
}

function fleetUsageSeries() {
  return buildFleetUsageSeries(state.usageHistory, visibleEndpointIds(), 180);
}

function fleetBillingSeries() {
  return buildFleetBillingSeries(state.billingHistory, visibleEndpointIds(), currentMonitoringWindow().preset === "months_6" ? 12 : 31);
}

function parseTimelineDate(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number") {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  const text = String(value).trim();
  if (!text) return null;
  if (/^\d+$/.test(text)) {
    const numericDate = new Date(Number(text));
    return Number.isNaN(numericDate.getTime()) ? null : numericDate;
  }
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatHistoryTime(value, { includeDate = false } = {}) {
  const date = parseTimelineDate(value);
  if (!date) return "-";
  const window = currentMonitoringWindow();
  return formatMonitoringTimestamp(date, {
    preset: window.preset,
    utcOffsetMinutes: window.utcOffsetMinutes,
    detailed: includeDate,
  });
}

function filterSamplesToCurrentWindow(samples = []) {
  const window = currentMonitoringWindow();
  const start = parseTimelineDate(window.startTime);
  const end = parseTimelineDate(window.endTime);
  if (!start || !end) return Array.isArray(samples) ? samples : [];
  return (Array.isArray(samples) ? samples : []).filter((sample) => {
    const sampleTime = parseTimelineDate(sample?.t || sample?.captured_at || sample?.bucket_start);
    return sampleTime && sampleTime >= start && sampleTime <= end;
  });
}

function formatMetricNumber(value, { integer = false } = {}) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric)) return "-";
  if (integer) return String(Math.round(numeric));
  if (Number.isInteger(numeric)) return String(numeric);
  return Math.abs(numeric) >= 10 ? numeric.toFixed(0) : numeric.toFixed(1);
}

function formatAxisMetricValue(value, { currency = false, integer = false } = {}) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric)) return "-";
  if (currency) {
    if (numeric >= 100) return `$${numeric.toFixed(0)}`;
    if (numeric >= 10) return `$${numeric.toFixed(1)}`;
    return `$${numeric.toFixed(2)}`;
  }
  return formatMetricNumber(numeric, { integer });
}

function summarizeSeriesMetric(samples = [], key) {
  const values = (Array.isArray(samples) ? samples : []).map((sample) => metricValue(sample, key));
  if (!values.length) {
    return { latest: 0, average: 0, peak: 0, total: 0 };
  }
  const total = values.reduce((sum, value) => sum + value, 0);
  return {
    latest: values[values.length - 1],
    average: total / values.length,
    peak: Math.max(...values),
    total,
  };
}

function renderTimelineAxis(samples) {
  const window = currentMonitoringWindow();
  const ticks = buildSparseMonitoringTickLabels(samples, {
    preset: window.preset,
    utcOffsetMinutes: window.utcOffsetMinutes,
    maxLabels: window.preset === "months_6" ? 6 : 7,
  });
  if (!ticks.length) return "";
  return `
    <div class="usage-axis mono" style="--axis-columns:${Math.max(ticks.length, 1)};">
      ${ticks.map((tick) => `<span title="${formatHistoryTime(tick.t, { includeDate: true })}">${tick.label}</span>`).join("")}
    </div>
  `;
}

function formatResolutionLabel(value, kind = "usage") {
  const current = String(value || "auto").trim().toLowerCase();
  if (current === "raw") return kind === "usage" ? "raw samples" : "raw buckets";
  if (current === "minute_5") return "5-minute rollup";
  if (current === "minute_15") return "15-minute rollup";
  if (current === "hour") return "hourly rollup";
  if (current === "day") return "daily rollup";
  if (current === "week") return "weekly rollup";
  if (current === "month") return "monthly rollup";
  return "auto rollup";
}

function metricValue(sample, key) {
  const value = Number(sample?.[key] || 0);
  return Number.isFinite(value) ? value : 0;
}

function buildUsagePath(samples, key, width, height, padding, maxValue) {
  if (!samples.length) return "";
  const safeMax = Math.max(Number(maxValue) || 0, 1);
  const xStep = samples.length > 1 ? (width - padding * 2) / (samples.length - 1) : 0;
  return samples
    .map((sample, index) => {
      const x = padding + index * xStep;
      const ratio = metricValue(sample, key) / safeMax;
      const y = height - padding - ratio * (height - padding * 2);
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function buildChartGrid(width, height, padding, ratios = [0.25, 0.5, 0.75]) {
  return ratios
    .map((ratio) => {
      const y = (height - padding) - ratio * (height - padding * 2);
      return `<line x1="${padding}" y1="${y.toFixed(2)}" x2="${width - padding}" y2="${y.toFixed(2)}" />`;
    })
    .join("");
}

function renderChartSvg(samples, series, { width = 420, height = 170, padding = 16, ariaLabel = "Chart", className = "usage-chart" } = {}) {
  if (!samples.length || !series.length) return "";
  const maxValue = Math.max(1, ...samples.flatMap((sample) => series.map((item) => metricValue(sample, item.key))));
  const lastSample = samples[samples.length - 1] || {};
  const xStep = samples.length > 1 ? (width - padding * 2) / (samples.length - 1) : 0;
  const grid = buildChartGrid(width, height, padding);
  const paths = series
    .map((item) => {
      const path = buildUsagePath(samples, item.key, width, height, padding, maxValue);
      return path ? `<path d="${path}" style="--series-color:${item.color}"></path>` : "";
    })
    .join("");
  const points = series
    .map((item) => {
      const x = samples.length > 1 ? padding + (samples.length - 1) * xStep : width / 2;
      const ratio = metricValue(lastSample, item.key) / maxValue;
      const y = height - padding - ratio * (height - padding * 2);
      return `<circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="${width > 260 ? 4 : 3}" style="--series-color:${item.color}"></circle>`;
    })
    .join("");

  return `
    <svg class="${className}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="${ariaLabel}">
      <g class="usage-chart-grid">${grid}</g>
      <g class="usage-chart-lines">${paths}</g>
      <g class="usage-chart-points">${points}</g>
    </svg>
  `;
}

function buildChartMaxValue(samples, series) {
  return Math.max(1, ...samples.flatMap((sample) => series.map((item) => metricValue(sample, item.key))));
}

function renderChartFrame(samples, series, { ariaLabel, currency = false, integer = false } = {}) {
  if (!samples.length || !series.length) return "";
  const scaleLabels = buildChartScaleLabels(buildChartMaxValue(samples, series), { integer: integer && !currency });
  return `
    <div class="chart-frame">
      <div class="chart-y-axis mono">
        ${scaleLabels.map((value) => `<span>${formatAxisMetricValue(value, { currency, integer })}</span>`).join("")}
      </div>
      <div class="chart-pane">
        <div class="usage-chart-shell">
          ${renderChartSvg(samples, series, { ariaLabel })}
        </div>
        ${renderTimelineAxis(samples)}
      </div>
    </div>
  `;
}

function renderChartStatGrid(cards = []) {
  if (!cards.length) return "";
  return `
    <div class="chart-stat-grid">
      ${cards
        .map((card) => `
          <article class="chart-stat-card">
            <div class="chart-stat-head">
              <span class="usage-legend-swatch" style="--swatch:${card.color}"></span>
              <strong>${card.label}</strong>
            </div>
            <div class="chart-stat-values">
              ${card.items
                .map((item) => `
                  <span>
                    <small>${item.label}</small>
                    <strong>${item.value}</strong>
                  </span>
                `)
                .join("")}
            </div>
          </article>
        `)
        .join("")}
    </div>
  `;
}

function renderUsageChart(endpoint) {
  const window = currentMonitoringWindow();
  const rawSamples = filterSamplesToCurrentWindow(endpointHistory(endpoint?.id));
  const samples = buildUsageChartSeries(rawSamples, {
    preset: window.preset,
    utcOffsetMinutes: window.utcOffsetMinutes,
  });
  if (samples.length < 2) {
    return `
      <div class="usage-card">
        <div class="usage-card-head">
          <strong>Usage trend</strong>
          <span class="muted">History fills as refreshes arrive.</span>
        </div>
        <div class="usage-empty">${rawSamples.length === 1 ? "Captured the first bucket. Refresh once more to draw the trend line." : "Refresh the dashboard a few times to build a trend line for the selected period."}</div>
      </div>
    `;
  }

  const series = [
    { key: "workers", label: "Workers", color: "#0b6f7b" },
    { key: "queued", label: "Queued", color: "#f3b74f" },
    { key: "running", label: "Running", color: "#2f9e44" },
  ].filter((item) => item.key === "workers" || endpoint?.data_source === "health" || samples.some((sample) => metricValue(sample, item.key) > 0));

  return `
    <div class="usage-card">
      <div class="usage-card-head">
        <strong>Usage trend</strong>
        <span class="muted">${monitoringRangeLabel()} · ${formatResolutionLabel(state.historyMeta?.window?.usage_resolution, "usage")}</span>
      </div>
      ${renderChartFrame(samples, series, { ariaLabel: "Endpoint usage chart", integer: true })}
      ${renderChartStatGrid(
        series.map((item) => {
          const summary = summarizeSeriesMetric(samples, item.key);
          return {
            label: item.label,
            color: item.color,
            items: [
              { label: "Latest", value: formatMetricNumber(summary.latest, { integer: true }) },
              { label: "Avg", value: formatMetricNumber(summary.average) },
              { label: "Peak", value: formatMetricNumber(summary.peak, { integer: true }) },
            ],
          };
        })
      )}
    </div>
  `;
}

function renderSpendChart(endpoint) {
  const billingMode = String(state.billing?.mode || "rest");
  const collector = state.historyMeta?.collector || {};
  const lastBillingSync = String(collector.last_billing_sync || "");
  const window = currentMonitoringWindow();
  const rawSamples = filterSamplesToCurrentWindow(endpointBillingSeries(endpoint?.id));
  const samples = buildSpendChartSeries(rawSamples, {
    preset: window.preset,
    utcOffsetMinutes: window.utcOffsetMinutes,
  });
  const hasHistoricalSpend = samples.length > 0;
  if (billingMode === "unavailable" && !hasHistoricalSpend) {
    return `
      <div class="usage-card">
        <div class="usage-card-head">
          <strong>Spend trend</strong>
          <span class="muted">Admin billing API required</span>
        </div>
        <div class="usage-empty">This RunPod key cannot read billing history, so spend charts stay unavailable until an admin-enabled key is configured.</div>
      </div>
    `;
  }

  if (!samples.length) {
    return `
      <div class="usage-card">
        <div class="usage-card-head">
          <strong>Spend trend</strong>
          <span class="muted">${monitoringRangeLabel()} · ${formatResolutionLabel(state.historyMeta?.window?.billing_resolution, "billing")}</span>
        </div>
        <div class="usage-empty">No billing buckets were returned for this endpoint in the selected window.</div>
      </div>
    `;
  }

  const spendSummary = summarizeSeriesMetric(samples, "cost");
  const summary = endpointBillingSummary(endpoint?.id);
  const records = Number(summary?.records || samples.reduce((sum, sample) => sum + Number(sample?.records || 0), 0));
  const series = [{ key: "cost", label: "Spend", color: "#c7841d" }];

  return `
    <div class="usage-card">
      <div class="usage-card-head">
        <strong>Spend trend</strong>
        <span class="muted">${billingMode === "unavailable" && lastBillingSync ? `Showing last synced billing history from ${formatDateTime(lastBillingSync)}` : `${monitoringRangeLabel()} · ${formatResolutionLabel(state.historyMeta?.window?.billing_resolution, "billing")}`}</span>
      </div>
      ${renderChartFrame(samples, series, { ariaLabel: "Endpoint spend chart", currency: true })}
      ${renderChartStatGrid([
        {
          label: "Spend",
          color: "#c7841d",
          items: [
            { label: billingMode === "unavailable" && hasHistoricalSpend ? "Latest synced" : "Latest", value: formatCurrency(spendSummary.latest) },
            { label: "Total", value: formatCurrency(spendSummary.total) },
            { label: window.preset === "months_6" ? "Peak month" : "Peak day", value: formatCurrency(spendSummary.peak) },
            { label: "Records", value: formatMetricNumber(records, { integer: true }) },
          ],
        },
      ])}
    </div>
  `;
}

function scrollToDetailPanel() {
  if (!el.detailPanel) return;
  window.requestAnimationFrame(() => {
    el.detailPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message = payload && typeof payload.error === "string" ? payload.error : `HTTP ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function apiCall(name, args = {}) {
  const payload = await fetchJson(`${state.apiBase}/tools/call`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ name, arguments: args }),
  });
  if (!payload?.ok) {
    throw new Error(payload?.error || "Unexpected API response");
  }
  return payload.result;
}

async function login() {
  const username = el.loginUsername.value.trim();
  const password = el.loginPassword.value.trim();
  if (!username || !password) {
    setBanner("Username and password are required.", "warn");
    return;
  }
  setLoading(true);
  try {
    const payload = await fetchJson(`${state.apiBase}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!payload?.ok || !payload?.token) {
      throw new Error(payload?.error || "Login failed");
    }
    state.user = payload.user || null;
    persistToken(payload.token);
    setBanner("Signed in. Loading RunPod fleet.", "success");
    await refreshDashboard();
  } finally {
    setLoading(false);
  }
}

async function pingApi({ quiet = false } = {}) {
  try {
    const payload = await fetchJson(`${state.apiBase}/healthz`);
    if (!quiet) {
      setBanner(payload?.ok ? "Pipeline API responded successfully." : "Pipeline API responded with an unexpected payload.", "success");
    }
    return true;
  } catch (error) {
    if (!quiet) {
      setBanner(`Pipeline API health check failed: ${error.message}`, "error");
    }
    return false;
  }
}

async function loadSession() {
  if (!state.token) return;
  try {
    const payload = await fetchJson(`${state.apiBase}/auth/me`, {
      headers: { ...authHeaders() },
    });
    if (payload?.ok) {
      state.user = payload.user || null;
    }
  } catch (_error) {
    state.user = null;
    persistToken("");
  }
}

function selectedEndpoint() {
  return state.detail?.endpoint || null;
}

function currentFilters() {
  return {
    managed_only: Boolean(el.managedOnlyToggle.checked),
    include_workers: Boolean(el.includeWorkersToggle.checked),
  };
}

async function refreshDashboard() {
  setLoading(true);
  const monitoringWindow = currentMonitoringWindow();
  const comparisonWindow = buildPreviousMonitoringWindow(monitoringWindow);
  try {
    const [fleet, billing, history, comparisonHistory] = await Promise.all([
      apiCall("pipeline.runpod_list_endpoints", currentFilters()),
      apiCall("pipeline.runpod_list_billing", {
        start_time: monitoringWindow.startTime,
        end_time: monitoringWindow.endTime,
        bucket_size: monitoringWindow.billingBucketSize,
      }),
      apiCall("pipeline.runpod_get_history", {
        start_time: monitoringWindow.startTime,
        end_time: monitoringWindow.endTime,
        usage_resolution: monitoringWindow.usageResolution,
        billing_resolution: monitoringWindow.billingResolution,
        limit: monitoringWindow.usageLimit,
      }),
      apiCall("pipeline.runpod_get_history", {
        start_time: comparisonWindow.startTime,
        end_time: comparisonWindow.endTime,
        usage_resolution: comparisonWindow.usageResolution,
        billing_resolution: comparisonWindow.billingResolution,
        limit: comparisonWindow.usageLimit,
      }),
    ]);
    state.fleet = fleet || state.fleet;
    state.billing = billing || state.billing;
    state.monitoringWindow = monitoringWindow;
    state.comparisonWindow = comparisonWindow;
    applyHistoryPayload(history);
    state.comparisonUsageHistory = normalizeUsageHistoryMap(comparisonHistory?.usage_history, comparisonHistory);
    state.comparisonBillingHistory = normalizeBillingHistoryMap(comparisonHistory?.billing_history);
    state.comparisonSummary = computeComparisonSummary();

    const visibleIds = (state.fleet.endpoints || []).map((item) => String(item.id || ""));
    if (state.selectedEndpointId && visibleIds.includes(state.selectedEndpointId)) {
      state.detail = await apiCall("pipeline.runpod_get_endpoint", {
        endpoint_id: state.selectedEndpointId,
        include_workers: true,
      });
      syncFormFromEndpoint(selectedEndpoint());
    } else {
      state.selectedEndpointId = "";
      state.detail = null;
      clearForm();
    }
    showApp(true);
    el.lastRefreshValue.textContent = `Updated ${new Date().toLocaleTimeString()}`;
    render();
    if (state.fleet?.read_only) {
      setBanner("This RunPod key can submit jobs and read /health, but RunPod denied endpoint admin and billing APIs. Health-only monitoring stays available, while spend and patch actions remain disabled.", "warn");
    } else if (state.billing?.mode === "unavailable") {
      setBanner("Endpoint billing is unavailable for this key.", "warn");
    } else {
      setBanner("", "info");
    }
  } catch (error) {
    handleApiError(error);
    throw error;
  } finally {
    setLoading(false);
  }
}

function handleApiError(error) {
  const rawMessage = String(error?.message || "Unknown error");
  const message = /RUNPOD_API_KEY was rejected by the RunPod API|401 Client Error: Unauthorized for url: https:\/\/rest\.runpod\.io/i.test(rawMessage)
    ? "RunPod API key is invalid or expired. Update RUNPOD_API_KEY in /opt/protein_pipeline/pipeline-mcp/.env and restart pipeline-mcp."
    : /RUNPOD_API_KEY does not have permission|403 Client Error: Forbidden for url: https:\/\/rest\.runpod\.io/i.test(rawMessage)
      ? "RunPod API key does not have permission for RunPod admin operations. Create a key with endpoint management/billing access and restart pipeline-mcp."
      : rawMessage;
  if (error?.status === 401 || /unauthorized/i.test(message)) {
    state.user = null;
    persistToken("");
    showApp(false);
    setBanner("Authentication is required for RunPod admin actions.", "warn");
    return;
  }
  if (error?.status === 403 || /admin required/i.test(message)) {
    showApp(false);
    setBanner("Admin role is required to use the RunPod control room.", "error");
    return;
  }
  setBanner(message, "error");
}

function formatScaleRange(endpoint) {
  return `${endpoint?.workers_min ?? 0} → ${endpoint?.workers_max ?? 0}`;
}

function formatManagedEnvVars(refs) {
  if (!Array.isArray(refs) || !refs.length) return "No pipeline mapping";
  return refs
    .map((item) => String(item?.env_var || item?.label || "").trim())
    .filter(Boolean)
    .join(" / ");
}

function formatDataCenters(endpoint) {
  const values = Array.isArray(endpoint?.data_center_ids) ? endpoint.data_center_ids : [];
  return values.length ? values.join(" · ") : "No data center hint";
}

function endpointCard(endpoint) {
  const endpointId = String(endpoint?.id || "").trim();
  const managed = formatManagedServices(endpoint.managed_services);
  const gpuTypes = Array.isArray(endpoint.gpu_types) && endpoint.gpu_types.length ? endpoint.gpu_types.join(", ") : "No GPU types";
  const status = deriveEndpointStatus(endpoint);
  const spendTotal = endpointBillingSeries(endpointId).reduce((sum, sample) => sum + metricValue(sample, "cost"), 0);

  return `
    <article class="endpoint-card endpoint-compact-card">
      <div class="endpoint-card-head">
        <div class="endpoint-card-title">
          <strong>${endpoint.name || endpointId}</strong>
          <span class="mono">${endpointId}</span>
        </div>
        <span class="status-pill ${status.tone}">${status.label}</span>
      </div>
      <div class="chip-row">
        <span class="chip ${endpoint.managed ? "managed" : ""}">${managed}</span>
        <span class="chip">${endpoint.compute_type || "serverless"}</span>
      </div>
      <div class="endpoint-card-kpis">
        <span><strong>Workers</strong>${endpoint.worker_summary?.total ?? 0}</span>
        <span><strong>Running</strong>${endpoint.health_jobs?.in_progress ?? 0}</span>
        <span><strong>Queued</strong>${endpoint.health_jobs?.in_queue ?? 0}</span>
        <span><strong>Period Spend</strong>${formatCurrency(spendTotal)}</span>
      </div>
      <p class="endpoint-card-copy">${gpuTypes}</p>
      <div class="endpoint-card-actions">
        <button class="ghost" type="button" data-select-endpoint-id="${endpointId}">Open endpoint</button>
      </div>
      <div class="endpoint-card-metrics">
        <span><strong>Scale</strong>${formatScaleRange(endpoint)}</span>
        <span><strong>Data centers</strong>${formatDataCenters(endpoint)}</span>
        <span><strong>Binding</strong>${formatManagedEnvVars(endpoint.managed_services)}</span>
      </div>
    </article>
  `;
}

function renderFleetUsageChart(samples) {
  const window = currentMonitoringWindow();
  const chartSamples = buildUsageChartSeries(filterSamplesToCurrentWindow(samples), {
    preset: window.preset,
    utcOffsetMinutes: window.utcOffsetMinutes,
  });
  if (chartSamples.length < 2) {
    return `
      <div class="usage-card">
        <div class="usage-card-head">
          <strong>Fleet usage</strong>
          <span class="muted">Waiting for more samples</span>
        </div>
        <div class="usage-empty">Refresh the dashboard a few times to build a fleet usage trend for the selected period.</div>
      </div>
    `;
  }

  const series = [
    { key: "workers", label: "Workers", color: "#0b6f7b" },
    { key: "queued", label: "Queued", color: "#f3b74f" },
    { key: "running", label: "Running", color: "#2f9e44" },
  ].filter((item) => item.key === "workers" || chartSamples.some((sample) => metricValue(sample, item.key) > 0));

  return `
    <div class="usage-card">
      <div class="usage-card-head">
        <strong>Fleet usage</strong>
        <span class="muted">${monitoringRangeLabel()} · ${formatResolutionLabel(state.historyMeta?.window?.usage_resolution, "usage")}</span>
      </div>
      ${renderChartFrame(chartSamples, series, { ariaLabel: "Fleet usage chart", integer: true })}
      ${renderChartStatGrid(
        series.map((item) => {
          const summary = summarizeSeriesMetric(chartSamples, item.key);
          return {
            label: item.label,
            color: item.color,
            items: [
              { label: "Latest", value: formatMetricNumber(summary.latest, { integer: true }) },
              { label: "Avg", value: formatMetricNumber(summary.average) },
              { label: "Peak", value: formatMetricNumber(summary.peak, { integer: true }) },
            ],
          };
        })
      )}
    </div>
  `;
}

function renderFleetSpendChart(samples) {
  const window = currentMonitoringWindow();
  const chartSamples = buildSpendChartSeries(filterSamplesToCurrentWindow(samples), {
    preset: window.preset,
    utcOffsetMinutes: window.utcOffsetMinutes,
  });
  if (!chartSamples.length) {
    return `
      <div class="usage-card">
        <div class="usage-card-head">
          <strong>Fleet spend</strong>
          <span class="muted">${monitoringRangeLabel()}</span>
        </div>
        <div class="usage-empty">No billing buckets were returned for the visible endpoint set.</div>
      </div>
    `;
  }

  const spendSummary = summarizeSeriesMetric(chartSamples, "cost");

  return `
    <div class="usage-card">
      <div class="usage-card-head">
        <strong>Fleet spend</strong>
        <span class="muted">${monitoringRangeLabel()} · ${formatResolutionLabel(state.historyMeta?.window?.billing_resolution, "billing")}</span>
      </div>
      ${renderChartFrame(chartSamples, [{ key: "cost", label: "Spend", color: "#c7841d" }], { ariaLabel: "Fleet spend chart", currency: true })}
      ${renderChartStatGrid([
        {
          label: "Spend",
          color: "#c7841d",
          items: [
            { label: "Latest", value: formatCurrency(spendSummary.latest) },
            { label: "Total", value: formatCurrency(spendSummary.total) },
            { label: window.preset === "months_6" ? "Peak month" : "Peak day", value: formatCurrency(spendSummary.peak) },
          ],
        },
      ])}
    </div>
  `;
}

function renderComparisonBoard() {
  const summary = state.comparisonSummary;
  if (!summary) return "";
  const spendDelta = formatDelta(summary.spend);
  const workersDelta = formatDelta(summary.avgWorkers);
  const runningDelta = formatDelta(summary.avgRunning);
  const queuedDelta = formatDelta(summary.peakQueued);

  return `
    <div class="comparison-strip">
      <span class="comparison-pill ${spendDelta.tone}">
        <strong>Spend</strong>
        <span>${formatCurrency(summary.spend.current)}</span>
        <em>${spendDelta.text}</em>
      </span>
      <span class="comparison-pill ${workersDelta.tone}">
        <strong>Avg workers</strong>
        <span>${summary.avgWorkers.current.toFixed(2)}</span>
        <em>${workersDelta.text}</em>
      </span>
      <span class="comparison-pill ${runningDelta.tone}">
        <strong>Avg running</strong>
        <span>${summary.avgRunning.current.toFixed(2)}</span>
        <em>${runningDelta.text}</em>
      </span>
      <span class="comparison-pill ${queuedDelta.tone}">
        <strong>Peak queued</strong>
        <span>${summary.peakQueued.current.toFixed(0)}</span>
        <em>${queuedDelta.text}</em>
      </span>
      <span class="comparison-caption">vs ${comparisonRangeLabel()}</span>
    </div>
  `;
}

function renderPeriodControls() {
  if (el.periodPresetSelect) {
    el.periodPresetSelect.value = state.periodPreset;
  }
  if (el.periodLabel) {
    el.periodLabel.textContent = monitoringRangeLabel();
  }
  if (el.periodNavNextBtn) {
    el.periodNavNextBtn.disabled = state.loading || !canNavigateToNextMonitoringWindow(currentMonitoringWindow());
  }
}

function renderServiceBoard() {
  const usageSamples = fleetUsageSeries();
  const spendSamples = fleetBillingSeries();
  const collector = state.historyMeta?.collector || {};
  const window = currentMonitoringWindow();

  el.serviceBoard.innerHTML = `
    <div class="overview-window-card">
      <div>
        <span class="status-pill warm">${window.title}</span>
        <strong>${monitoringRangeLabel()}</strong>
      </div>
      <span class="muted">Fleet usage, spend, CSV exports, and backend queries all share this calendar window.</span>
    </div>
    ${renderComparisonBoard()}
    <div class="analytics-grid">
      <div id="fleetUsageChartCard">${renderFleetUsageChart(usageSamples)}</div>
      <div id="fleetSpendChartCard">${renderFleetSpendChart(spendSamples)}</div>
    </div>
    <div class="download-row">
      <button class="ghost" type="button" data-download="fleet-usage-csv">Download usage CSV</button>
      <button class="ghost" type="button" data-download="fleet-billing-csv">Download billing CSV</button>
      <button class="ghost" type="button" data-download="fleet-usage-svg">Usage SVG</button>
      <button class="ghost" type="button" data-download="fleet-spend-svg">Spend SVG</button>
      <span class="muted">Last sync: ${collector.last_usage_sync ? formatDateTime(collector.last_usage_sync) : "not yet synced"}</span>
    </div>
  `;
}

function renderEndpointScopeSelector() {
  const scopes = buildEndpointScopeOptions(state.fleet?.managed_services || [], state.fleet?.endpoints || []);
  const activeEndpointId = String(state.selectedEndpointId || "").trim();
  el.endpointScopeSelector.innerHTML = scopes.length
    ? scopes
        .map((scope) => {
          const isAll = scope.endpointId === "all";
          const active = isAll ? !activeEndpointId : activeEndpointId === scope.endpointId;
          const attrs = isAll
            ? `data-select-scope="all" aria-pressed="${active ? "true" : "false"}"`
            : scope.available
              ? `data-select-endpoint-id="${scope.endpointId}" aria-pressed="${active ? "true" : "false"}"`
              : 'disabled aria-disabled="true"';
          return `
            <button class="service-map-chip ${scope.available ? "linked" : "missing"} ${active ? "active" : ""}" type="button" ${attrs}>
              <strong>${scope.label}</strong>
              <span class="mono">${scope.subtitle}</span>
            </button>
          `;
        })
        .join("")
    : '<div class="empty-state">No managed endpoint mappings are configured.</div>';
}

function renderMissingEndpoints() {
  const missing = Array.isArray(state.fleet?.missing_endpoints) ? state.fleet.missing_endpoints : [];
  if (!missing.length) {
    el.missingEndpoints.innerHTML = "";
    return;
  }
  el.missingEndpoints.innerHTML = missing
    .map(
      (item) => `
        <article class="warning-card">
          <strong>Configured but missing</strong>
          <span class="mono">${item.endpoint_id}</span>
          <span>${formatManagedServices(item.managed_services)}</span>
        </article>
      `
    )
    .join("");
}

function renderEndpointList() {
  const endpoints = Array.isArray(state.fleet?.endpoints) ? state.fleet.endpoints : [];
  if (!endpoints.length) {
    el.endpointList.innerHTML = `<div class="empty-state">No endpoints matched the current filter.</div>`;
    return;
  }
  el.endpointList.innerHTML = endpoints.map(endpointCard).join("");
}

function renderEndpointWorkspace() {
  const endpoint = selectedEndpoint();
  const hasSelection = Boolean(state.selectedEndpointId && endpoint);

  if (el.endpointStageTitle) {
    el.endpointStageTitle.textContent = hasSelection ? endpoint.name || endpoint.id : "All endpoints";
  }
  if (el.endpointStageDescription) {
    el.endpointStageDescription.textContent = hasSelection
      ? "Single-endpoint mode keeps charts, workers, downloads, and patch controls in one place."
      : "Compact fleet cards in All. Single-endpoint detail appears here after selection.";
  }

  el.endpointList.classList.toggle("hidden", hasSelection);
  el.detailPanel.classList.toggle("hidden", !hasSelection);
  renderEndpointList();
  renderDetail();
}

function ensureSelectValue(select, value) {
  if (!value) return;
  const values = Array.from(select.options).map((option) => option.value);
  if (!values.includes(value)) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  }
  select.value = value;
}

function clearForm() {
  el.fieldName.value = "";
  el.fieldGpuTypeIds.value = "";
  el.fieldDataCenterIds.value = "";
  el.fieldTemplateId.value = "";
  el.fieldNetworkVolumeId.value = "";
  el.fieldScalerType.value = "REQUEST_COUNT";
  el.fieldScalerValue.value = "0";
  el.fieldWorkersMin.value = "0";
  el.fieldWorkersMax.value = "0";
  el.fieldIdleTimeout.value = "0";
  el.fieldExecutionTimeoutMs.value = "0";
  el.fieldFlashBoot.checked = false;
}

function syncFormFromEndpoint(endpoint) {
  if (!endpoint) {
    clearForm();
    return;
  }
  const form = prepareEndpointForm(endpoint);
  el.fieldName.value = form.name;
  el.fieldGpuTypeIds.value = form.gpuTypeIds;
  el.fieldDataCenterIds.value = form.dataCenterIds;
  el.fieldTemplateId.value = form.templateId;
  el.fieldNetworkVolumeId.value = form.networkVolumeId;
  ensureSelectValue(el.fieldScalerType, form.scalerType || "REQUEST_COUNT");
  el.fieldScalerValue.value = String(form.scalerValue ?? 0);
  el.fieldWorkersMin.value = String(form.workersMin ?? 0);
  el.fieldWorkersMax.value = String(form.workersMax ?? 0);
  el.fieldIdleTimeout.value = String(form.idleTimeout ?? 0);
  el.fieldExecutionTimeoutMs.value = String(form.executionTimeoutMs ?? 0);
  el.fieldFlashBoot.checked = Boolean(form.flashBoot);
}

function readForm() {
  return {
    name: el.fieldName.value,
    gpuTypeIds: el.fieldGpuTypeIds.value,
    dataCenterIds: el.fieldDataCenterIds.value,
    templateId: el.fieldTemplateId.value,
    networkVolumeId: el.fieldNetworkVolumeId.value,
    scalerType: el.fieldScalerType.value,
    scalerValue: el.fieldScalerValue.value,
    workersMin: el.fieldWorkersMin.value,
    workersMax: el.fieldWorkersMax.value,
    idleTimeout: el.fieldIdleTimeout.value,
    executionTimeoutMs: el.fieldExecutionTimeoutMs.value,
    flashBoot: el.fieldFlashBoot.checked,
  };
}

function renderDetail() {
  const endpoint = selectedEndpoint();
  if (!endpoint) {
    el.detailTitle.textContent = "Selected endpoint";
    el.detailHero.innerHTML = `<div class="empty-state">Choose an endpoint to open its monitoring sheet.</div>`;
    el.detailMeta.innerHTML = `<div class="empty-state">Choose an endpoint above to inspect current state, worker inventory, and patch controls.</div>`;
    el.detailDownloads.innerHTML = "";
    el.detailCharts.innerHTML = "";
    el.workerSummary.innerHTML = "";
    el.workerRows.innerHTML = `<tr><td colspan="4" class="empty-cell">No endpoint selected.</td></tr>`;
    return;
  }

  const template = endpoint.template || {};
  const cost = endpoint.worker_summary?.hourly_cost;
  const workerStates = endpoint.worker_summary?.states || {};
  const jobs = endpoint.health_jobs || {};
  const status = deriveEndpointStatus(endpoint);
  const workerStateSummary = Object.entries(workerStates)
    .map(([key, value]) => `<span class="chip">${key}:${value}</span>`)
    .join("");
  const readOnlyNote = endpoint.read_only
    ? `<p class="detail-hero-note">Health-only monitoring mode. RunPod denied admin write/billing APIs for this key.</p>`
    : "";
  const summary = endpointBillingSummary(endpoint.id);
  const spendTotal = Number(summary?.cost || endpointBillingSeries(endpoint.id).reduce((sum, sample) => sum + metricValue(sample, "cost"), 0));

  el.detailTitle.textContent = endpoint.name || endpoint.id;
  el.detailHero.innerHTML = `
    <div class="detail-hero-main">
      <div class="detail-hero-copy">
        <p class="eyebrow">Endpoint Detail</p>
        <div class="detail-status-row">
          <span class="status-pill ${status.tone}">${status.label}</span>
          <span class="mono">${endpoint.id}</span>
        </div>
        <h3>${endpoint.name || endpoint.id}</h3>
        <p class="detail-hero-note">protein_pipeline binding: ${formatManagedEnvVars(endpoint.managed_services)}</p>
        ${readOnlyNote}
      </div>
      <div class="detail-hero-kpis">
        <article>
          <span>Scale</span>
          <strong>${formatScaleRange(endpoint)}</strong>
        </article>
        <article>
          <span>Scaler</span>
          <strong>${endpoint.scaler_type || "-"} / ${endpoint.scaler_value ?? 0}</strong>
        </article>
        <article>
          <span>Queued jobs</span>
          <strong>${jobs.in_queue ?? 0}</strong>
        </article>
        <article>
          <span>Running jobs</span>
          <strong>${jobs.in_progress ?? 0}</strong>
        </article>
      </div>
    </div>
  `;
  el.detailMeta.innerHTML = `
    <article class="meta-card">
      <span class="meta-label">Status</span>
      <strong>${status.label}</strong>
    </article>
    <article class="meta-card">
      <span class="meta-label">Endpoint ID</span>
      <strong class="mono">${endpoint.id}</strong>
    </article>
    <article class="meta-card">
      <span class="meta-label">Managed by</span>
      <strong>${formatManagedServices(endpoint.managed_services)}</strong>
    </article>
    <article class="meta-card">
      <span class="meta-label">Template</span>
      <strong>${template.name || template.id || "-"}</strong>
    </article>
    <article class="meta-card">
      <span class="meta-label">Source</span>
      <strong>${endpoint.data_source === "health" ? "Health fallback" : "RunPod admin API"}</strong>
    </article>
    <article class="meta-card">
      <span class="meta-label">Workers</span>
      <strong>${endpoint.workers_min ?? 0} → ${endpoint.workers_max ?? 0}</strong>
    </article>
    <article class="meta-card">
      <span class="meta-label">Period Spend</span>
      <strong>${formatCurrency(spendTotal)}</strong>
    </article>
    <article class="meta-card">
      <span class="meta-label">Hourly worker cost</span>
      <strong>${formatCurrency(cost)}</strong>
    </article>
  `;
  el.detailDownloads.innerHTML = `
    <button class="ghost" type="button" data-download="endpoint-usage-csv" data-endpoint-id="${endpoint.id}">Download usage CSV</button>
    <button class="ghost" type="button" data-download="endpoint-billing-csv" data-endpoint-id="${endpoint.id}">Download billing CSV</button>
    <button class="ghost" type="button" data-download="endpoint-usage-svg" data-endpoint-id="${endpoint.id}">Usage SVG</button>
    <button class="ghost" type="button" data-download="endpoint-spend-svg" data-endpoint-id="${endpoint.id}">Spend SVG</button>
  `;
  el.detailCharts.innerHTML = `
    <div id="detailUsageChartCard">${renderUsageChart(endpoint)}</div>
    <div id="detailSpendChartCard">${renderSpendChart(endpoint)}</div>
  `;

  el.workerSummary.innerHTML = `
    <div class="chip-row">${workerStateSummary || `<span class="chip">no workers</span>`}</div>
    <div class="worker-summary-line">
      <span class="mono">Data centers: ${formatDataCenters(endpoint)}</span>
      <span class="mono">Pipeline mapping: ${formatManagedEnvVars(endpoint.managed_services)}</span>
    </div>
  `;

  const workers = Array.isArray(endpoint.workers) ? endpoint.workers : [];
  el.workerRows.innerHTML = workers.length
    ? workers
        .map(
          (worker) => `
            <tr>
              <td class="mono">${worker.id || "-"}</td>
              <td>${worker.status || "-"}</td>
              <td>${(worker.gpu_types || []).join(", ") || "-"}</td>
              <td>${formatCurrency(worker.cost_per_hr)}</td>
            </tr>
          `
        )
        .join("")
    : `<tr><td colspan="4" class="empty-cell">${endpoint.data_source === "health" ? "Health API reports aggregate worker counts only; per-worker pod rows are unavailable." : "RunPod did not return live workers for this endpoint."}</td></tr>`;
}

function renderSummary() {
  const billingRows = billingRowsForVisibleEndpoints();
  const fleetSummary = summarizeFleet(state.fleet?.endpoints || [], billingRows);
  const billingMode = String(state.billing?.mode || "rest");
  el.summaryEndpoints.textContent = `${fleetSummary.totalEndpoints} / ${fleetSummary.managedEndpoints}`;
  el.summaryWorkers.textContent = `${fleetSummary.workersMin} / ${fleetSummary.workersMax}`;
  el.summaryLiveWorkers.textContent = String(fleetSummary.liveWorkers);
  el.summaryLoad.textContent = `${fleetSummary.runningJobs} / ${fleetSummary.queuedJobs}`;
  el.summaryCost.textContent = formatSpendSummary(fleetSummary.totalCost, billingMode);
  if (billingMode === "unavailable") {
    el.summaryCost.title = "Billing requires RunPod admin API access for this key.";
  } else {
    el.summaryCost.removeAttribute("title");
  }
}

function renderBilling() {
  if (state.billing?.mode === "unavailable") {
    el.billingSummaryCards.innerHTML = `
      <article class="billing-card">
        <span class="summary-label">Billing</span>
        <strong>Unavailable for this key</strong>
      </article>
      <article class="billing-card">
        <span class="summary-label">Reason</span>
        <strong>RunPod admin API permission required</strong>
      </article>
    `;
    el.billingRows.innerHTML = `<tr><td colspan="4" class="empty-cell">Billing history requires RunPod admin API access for this key.</td></tr>`;
    return;
  }

  const rows = billingHistoryRows();
  const totalCost = rows.reduce((sum, row) => sum + Number(row?.cost || 0), 0);
  const managedEndpointIds = new Set(
    (Array.isArray(state.fleet?.endpoints) ? state.fleet.endpoints : [])
      .filter((endpoint) => Array.isArray(endpoint?.managed_services) && endpoint.managed_services.length)
      .map((endpoint) => String(endpoint?.id || "").trim())
      .filter(Boolean)
  );
  const managedCost = rows
    .filter((row) => managedEndpointIds.has(String(row?.endpoint_id || "").trim()))
    .reduce((sum, row) => sum + Number(row.cost || 0), 0);

  el.billingSummaryCards.innerHTML = `
    <article class="billing-card">
      <span class="summary-label">Window</span>
      <strong>${monitoringRangeLabel()}</strong>
    </article>
    <article class="billing-card">
      <span class="summary-label">Total spend</span>
      <strong>${formatCurrency(totalCost)}</strong>
    </article>
    <article class="billing-card">
      <span class="summary-label">Managed spend</span>
      <strong>${formatCurrency(managedCost)}</strong>
    </article>
  `;

  el.billingRows.innerHTML = rows.length
    ? rows
        .map(
          (row) => `
            <tr>
              <td class="mono">${row.endpoint_id}</td>
              <td>${row.endpoint_name || "-"}</td>
              <td>${formatCurrency(row.cost)}</td>
              <td>${row.records}</td>
            </tr>
          `
        )
        .join("")
    : `<tr><td colspan="4" class="empty-cell">No billing buckets were returned for the selected calendar window.</td></tr>`;
}

function render() {
  setSessionBadge();
  renderPeriodControls();
  renderSummary();
  renderServiceBoard();
  renderEndpointScopeSelector();
  renderMissingEndpoints();
  renderEndpointWorkspace();
  renderBilling();
}

function selectAllEndpoints() {
  state.selectedEndpointId = "";
  state.detail = null;
  clearForm();
  render();
}

async function selectEndpoint(endpointId, { scrollIntoView = false } = {}) {
  if (!endpointId) return;
  state.selectedEndpointId = String(endpointId);
  setLoading(true);
  try {
    state.detail = await apiCall("pipeline.runpod_get_endpoint", {
      endpoint_id: state.selectedEndpointId,
      include_workers: true,
    });
    syncFormFromEndpoint(selectedEndpoint());
    render();
    if (scrollIntoView) {
      scrollToDetailPanel();
    }
  } catch (error) {
    handleApiError(error);
  } finally {
    setLoading(false);
  }
}

function navigateMonitoringWindow(direction) {
  state.monitoringWindow = shiftMonitoringWindow(currentMonitoringWindow(), direction);
  refreshDashboard().catch(() => {});
}

async function saveConfig() {
  const endpoint = selectedEndpoint();
  if (!endpoint) return;
  let patch;
  try {
    patch = buildEndpointPatch(readForm());
  } catch (error) {
    setBanner(error.message, "warn");
    return;
  }
  setLoading(true);
  try {
    state.detail = await apiCall("pipeline.runpod_update_endpoint", {
      endpoint_id: endpoint.id,
      patch,
    });
    syncFormFromEndpoint(selectedEndpoint());
    setBanner("RunPod endpoint patch applied. Refreshing dashboard.", "success");
    await refreshDashboard();
  } catch (error) {
    handleApiError(error);
  } finally {
    setLoading(false);
  }
}

function quickPatchFor(endpoint, mode) {
  const workersMin = Number(endpoint?.workers_min ?? 0);
  const workersMax = Number(endpoint?.workers_max ?? 0);
  if (mode === "pause") {
    return { workersMin: 0, workersMax: 0 };
  }
  if (mode === "warm") {
    return { workersMin: Math.max(workersMin, 1), workersMax: Math.max(workersMax, 1) };
  }
  return { workersMax: Math.max(workersMax, 4) };
}

async function applyQuickAction(mode) {
  const endpoint = selectedEndpoint();
  if (!endpoint) return;
  setLoading(true);
  try {
    await apiCall("pipeline.runpod_update_endpoint", {
      endpoint_id: endpoint.id,
      patch: quickPatchFor(endpoint, mode),
    });
    setBanner(`Applied quick action: ${mode}.`, "success");
    await refreshDashboard();
  } catch (error) {
    handleApiError(error);
  } finally {
    setLoading(false);
  }
}

function handleDownload(kind, endpointId = "") {
  const safeEndpointId = String(endpointId || "").trim();
  const endpointNames = endpointNameMap();
  const suffix = monitoringDownloadSuffix();

  if (kind === "fleet-billing-csv") {
    const rows = billingHistoryRows();
    downloadTextFile(`runpod-fleet-billing-${suffix}.csv`, buildBillingCsv(rows, endpointNames), "text/csv;charset=utf-8");
    return;
  }
  if (kind === "fleet-usage-csv") {
    downloadTextFile(`runpod-fleet-usage-${suffix}.csv`, buildUsageCsv(state.usageHistory, visibleEndpointIds()), "text/csv;charset=utf-8");
    return;
  }
  if (kind === "endpoint-billing-csv" && safeEndpointId) {
    downloadTextFile(
      `runpod-endpoint-${safeEndpointId}-billing-${suffix}.csv`,
      buildBillingCsv(billingHistoryRows(safeEndpointId), endpointNames),
      "text/csv;charset=utf-8"
    );
    return;
  }
  if (kind === "endpoint-usage-csv" && safeEndpointId) {
    downloadTextFile(
      `runpod-endpoint-${safeEndpointId}-usage-${suffix}.csv`,
      buildUsageCsv(state.usageHistory, [safeEndpointId]),
      "text/csv;charset=utf-8"
    );
    return;
  }
  if (kind === "fleet-usage-svg") {
    downloadChartSvg("fleetUsageChartCard", `runpod-fleet-usage-${suffix}.svg`);
    return;
  }
  if (kind === "fleet-spend-svg") {
    downloadChartSvg("fleetSpendChartCard", `runpod-fleet-spend-${suffix}.svg`);
    return;
  }
  if (kind === "endpoint-usage-svg" && safeEndpointId) {
    downloadChartSvg("detailUsageChartCard", `runpod-endpoint-${safeEndpointId}-usage-${suffix}.svg`);
    return;
  }
  if (kind === "endpoint-spend-svg" && safeEndpointId) {
    downloadChartSvg("detailSpendChartCard", `runpod-endpoint-${safeEndpointId}-spend-${suffix}.svg`);
  }
}

function startAutoRefresh() {
  if (state.refreshTimer) {
    window.clearInterval(state.refreshTimer);
  }
  state.refreshTimer = window.setInterval(() => {
    if (!el.appShell.classList.contains("hidden")) {
      refreshDashboard().catch(() => {});
    }
  }, 30000);
}

function bindEvents() {
  el.apiBaseInput.value = state.apiBase;
  if (el.periodPresetSelect) el.periodPresetSelect.value = state.periodPreset;
  el.homeBtn.addEventListener("click", () => {
    window.location.href = mainConsolePath();
  });
  el.saveApiBaseBtn.addEventListener("click", async () => {
    persistApiBase();
    if (await pingApi({ quiet: true })) {
      setBanner("Saved API base and confirmed health.", "success");
    } else {
      setBanner("Saved API base, but the health check failed.", "warn");
    }
  });
  el.pingApiBtn.addEventListener("click", () => {
    persistApiBase();
    pingApi();
  });
  el.refreshBtn.addEventListener("click", async () => {
    persistApiBase();
    await refreshDashboard().catch(() => {});
  });
  el.loginBtn.addEventListener("click", () => {
    persistApiBase();
    login().catch((error) => handleApiError(error));
  });
  el.loginBypassBtn.addEventListener("click", async () => {
    persistApiBase();
    persistToken("");
    state.user = null;
    await refreshDashboard().catch(() => {});
  });
  el.logoutBtn.addEventListener("click", () => {
    state.user = null;
    persistToken("");
    state.detail = null;
    showApp(false);
    setBanner("Logged out.", "info");
  });
  el.managedOnlyToggle.addEventListener("change", () => {
    refreshDashboard().catch(() => {});
  });
  el.includeWorkersToggle.addEventListener("change", () => {
    refreshDashboard().catch(() => {});
  });
  el.periodPresetSelect.addEventListener("change", () => {
    persistPeriodPreset();
    refreshDashboard().catch(() => {});
  });
  if (el.periodNavPrevBtn) {
    el.periodNavPrevBtn.addEventListener("click", () => {
      navigateMonitoringWindow(-1);
    });
  }
  if (el.periodNavNextBtn) {
    el.periodNavNextBtn.addEventListener("click", () => {
      if (!canNavigateToNextMonitoringWindow(currentMonitoringWindow())) return;
      navigateMonitoringWindow(1);
    });
  }
  el.endpointScopeSelector.addEventListener("click", (event) => {
    const scopeButton = event.target.closest("[data-select-scope]");
    if (scopeButton?.dataset.selectScope === "all") {
      selectAllEndpoints();
      return;
    }
    const button = event.target.closest("[data-select-endpoint-id]");
    if (!button) return;
    selectEndpoint(button.dataset.selectEndpointId, { scrollIntoView: shouldScrollDetailIntoView(window.innerWidth) });
  });
  el.endpointList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-select-endpoint-id]");
    if (!button) return;
    selectEndpoint(button.dataset.selectEndpointId, { scrollIntoView: shouldScrollDetailIntoView(window.innerWidth) });
  });
  el.appShell.addEventListener("click", (event) => {
    const button = event.target.closest("[data-download]");
    if (!button) return;
    handleDownload(button.dataset.download, button.dataset.endpointId);
  });
  el.resetConfigBtn.addEventListener("click", () => syncFormFromEndpoint(selectedEndpoint()));
  el.saveConfigBtn.addEventListener("click", () => saveConfig());
  el.quickWarmBtn.addEventListener("click", () => applyQuickAction("warm"));
  el.quickBurstBtn.addEventListener("click", () => applyQuickAction("burst"));
  el.quickPauseBtn.addEventListener("click", () => applyQuickAction("pause"));
}

async function bootstrap() {
  bindEvents();
  setSessionBadge();
  startAutoRefresh();
  persistApiBase();
  const healthy = await pingApi({ quiet: true });
  if (!healthy) {
    showApp(false);
    setBanner("Pipeline API is not reachable yet. Check the API base and health.", "warn");
    return;
  }
  await loadSession();
  try {
    await refreshDashboard();
  } catch (_error) {
    showApp(false);
  }
}

bootstrap().catch((error) => {
  handleApiError(error);
});
