export function normalizeApiBase(value) {
  return String(value || "").trim().replace(/\/$/, "");
}

export function resolveDefaultApiBase({
  origin = typeof window !== "undefined" ? window.location.origin : "",
  pathname = typeof window !== "undefined" ? window.location.pathname : "",
} = {}) {
  if (origin && origin !== "null" && pathname.startsWith("/pipeline")) {
    return `${origin}/pipeline/api`;
  }
  if (origin && /localhost|127\.0\.0\.1/.test(origin)) {
    return "http://127.0.0.1:18080";
  }
  if (origin && origin !== "null") {
    return `${origin}/api`;
  }
  return "http://127.0.0.1:18080";
}

export function splitCsvList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function formatManagedServices(services) {
  if (!Array.isArray(services) || !services.length) return "Unassigned";
  return services
    .map((item) => String(item?.label || item?.key || "").trim())
    .filter(Boolean)
    .join(" / ");
}

export function formatCurrency(value) {
  if (!Number.isFinite(Number(value))) return "-";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value));
}

export function formatSpendSummary(value, billingMode = "rest") {
  if (billingMode === "unavailable") return "Unavailable";
  return formatCurrency(value);
}

export function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || "-");
  return date.toLocaleString();
}

function isoUtcSeconds(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString().replace(".000Z", "Z");
}

function resolveUtcOffsetMinutes(now, utcOffsetMinutes) {
  if (Number.isFinite(Number(utcOffsetMinutes))) return Number(utcOffsetMinutes);
  const current = now instanceof Date ? now : new Date(now);
  return Number.isNaN(current.getTime()) ? 0 : -current.getTimezoneOffset();
}

function shiftToOffset(date, utcOffsetMinutes) {
  return new Date(date.getTime() + utcOffsetMinutes * 60_000);
}

function localBoundaryUtcDate(year, month, day, hour, minute, second, utcOffsetMinutes) {
  return new Date(Date.UTC(year, month, day, hour, minute, second) - utcOffsetMinutes * 60_000);
}

function formatShiftedDate(date, options) {
  return new Intl.DateTimeFormat("en-US", { ...options, timeZone: "UTC" }).format(date);
}

export function buildMonitoringWindow(preset = "week", { now = new Date(), utcOffsetMinutes } = {}) {
  const current = now instanceof Date ? new Date(now.getTime()) : new Date(now);
  if (Number.isNaN(current.getTime())) {
    throw new Error("now must be a valid date.");
  }

  const offset = resolveUtcOffsetMinutes(current, utcOffsetMinutes);
  const shifted = shiftToOffset(current, offset);
  const year = shifted.getUTCFullYear();
  const month = shifted.getUTCMonth();
  const day = shifted.getUTCDate();
  const weekday = shifted.getUTCDay();

  if (String(preset || "").trim() === "months_6") {
    const start = localBoundaryUtcDate(year, month - 5, 1, 0, 0, 0, offset);
    const end = localBoundaryUtcDate(year, month + 1, 0, 23, 59, 59, offset);
    return {
      preset: "months_6",
      title: "6 months",
      startTime: isoUtcSeconds(start),
      endTime: isoUtcSeconds(end),
      usageResolution: "month",
      billingResolution: "month",
      billingBucketSize: "day",
      usageLimit: 12,
      utcOffsetMinutes: offset,
    };
  }

  if (String(preset || "").trim() === "month") {
    const start = localBoundaryUtcDate(year, month, 1, 0, 0, 0, offset);
    const end = localBoundaryUtcDate(year, month + 1, 0, 23, 59, 59, offset);
    return {
      preset: "month",
      title: "Month",
      startTime: isoUtcSeconds(start),
      endTime: isoUtcSeconds(end),
      usageResolution: "day",
      billingResolution: "day",
      billingBucketSize: "day",
      usageLimit: 40,
      utcOffsetMinutes: offset,
    };
  }

  const mondayOffset = (weekday + 6) % 7;
  const start = localBoundaryUtcDate(year, month, day - mondayOffset, 0, 0, 0, offset);
  const end = localBoundaryUtcDate(year, month, day - mondayOffset + 6, 23, 59, 59, offset);
  return {
    preset: "week",
    title: "Week",
    startTime: isoUtcSeconds(start),
    endTime: isoUtcSeconds(end),
    usageResolution: "day",
    billingResolution: "day",
    billingBucketSize: "day",
    usageLimit: 16,
    utcOffsetMinutes: offset,
  };
}

export function shiftMonitoringWindow(window = {}, direction = 0) {
  const preset = String(window?.preset || "week").trim() || "week";
  const offset = resolveUtcOffsetMinutes(new Date(), window?.utcOffsetMinutes);
  const startParsed = parseTimelineValue(window?.startTime);
  if (startParsed === null) {
    return buildMonitoringWindow(preset, { utcOffsetMinutes: offset });
  }

  const start = shiftToOffset(new Date(startParsed), offset);
  let anchor;
  if (preset === "week") {
    anchor = localBoundaryUtcDate(
      start.getUTCFullYear(),
      start.getUTCMonth(),
      start.getUTCDate() + 7 * Number(direction || 0),
      12,
      0,
      0,
      offset
    );
  } else if (preset === "month") {
    anchor = localBoundaryUtcDate(
      start.getUTCFullYear(),
      start.getUTCMonth() + Number(direction || 0),
      15,
      12,
      0,
      0,
      offset
    );
  } else {
    const endParsed = parseTimelineValue(window?.endTime);
    const end = endParsed === null ? start : shiftToOffset(new Date(endParsed), offset);
    anchor = localBoundaryUtcDate(
      end.getUTCFullYear(),
      end.getUTCMonth() + Number(direction || 0),
      15,
      12,
      0,
      0,
      offset
    );
  }
  return buildMonitoringWindow(preset, { now: anchor, utcOffsetMinutes: offset });
}

export function buildPreviousMonitoringWindow(window = {}) {
  return shiftMonitoringWindow(window, -1);
}

export function canNavigateToNextMonitoringWindow(window = {}, { now = new Date(), utcOffsetMinutes } = {}) {
  const preset = String(window?.preset || "week").trim() || "week";
  const offset = resolveUtcOffsetMinutes(now instanceof Date ? now : new Date(now), utcOffsetMinutes ?? window?.utcOffsetMinutes);
  const current = buildMonitoringWindow(preset, { now, utcOffsetMinutes: offset });
  const selectedEnd = parseTimelineValue(window?.endTime);
  const currentEnd = parseTimelineValue(current?.endTime);
  if (selectedEnd === null || currentEnd === null) return false;
  return selectedEnd < currentEnd;
}

export function shouldScrollDetailIntoView(viewportWidth = 0) {
  const width = Number(viewportWidth || 0);
  return width > 0 && width < 960;
}

function averageOf(samples = [], key) {
  const rows = Array.isArray(samples) ? samples : [];
  if (!rows.length) return 0;
  return rows.reduce((sum, sample) => sum + Number(sample?.[key] || 0), 0) / rows.length;
}

function sumOf(samples = [], key) {
  return (Array.isArray(samples) ? samples : []).reduce((sum, sample) => sum + Number(sample?.[key] || 0), 0);
}

function peakOf(samples = [], key) {
  return Math.max(0, ...(Array.isArray(samples) ? samples : []).map((sample) => Number(sample?.[key] || 0)));
}

function buildDeltaMetric(current, previous, precision = 2) {
  const scale = 10 ** precision;
  const safeCurrent = Math.round(Number(current || 0) * scale) / scale;
  const safePrevious = Math.round(Number(previous || 0) * scale) / scale;
  const delta = Math.round((safeCurrent - safePrevious) * scale) / scale;
  const deltaPct = safePrevious === 0 ? (safeCurrent === 0 ? 0 : 100) : Math.round((delta / safePrevious) * 10_000) / 100;
  return { current: safeCurrent, previous: safePrevious, delta, deltaPct };
}

export function summarizeMonitoringComparison({
  currentUsage = [],
  previousUsage = [],
  currentBilling = [],
  previousBilling = [],
} = {}) {
  return {
    spend: buildDeltaMetric(sumOf(currentBilling, "cost"), sumOf(previousBilling, "cost")),
    avgWorkers: buildDeltaMetric(averageOf(currentUsage, "workers"), averageOf(previousUsage, "workers")),
    avgRunning: buildDeltaMetric(averageOf(currentUsage, "running"), averageOf(previousUsage, "running")),
    peakQueued: buildDeltaMetric(peakOf(currentUsage, "queued"), peakOf(previousUsage, "queued")),
  };
}

export function buildWindowDownloadSuffix(window = {}) {
  const preset = String(window?.preset || "week").trim() || "week";
  const utcOffsetMinutes = resolveUtcOffsetMinutes(new Date(), window?.utcOffsetMinutes);
  const startParsed = parseTimelineValue(window?.startTime);
  const endParsed = parseTimelineValue(window?.endTime);
  const startDate = startParsed === null ? null : shiftToOffset(new Date(startParsed), utcOffsetMinutes);
  const endDate = endParsed === null ? null : shiftToOffset(new Date(endParsed), utcOffsetMinutes);
  const start = startDate ? isoUtcSeconds(startDate).slice(0, 10) : "";
  const end = endDate ? isoUtcSeconds(endDate).slice(0, 10) : "";
  if (preset === "months_6") {
    return `months-6-${start.slice(0, 7)}-to-${end.slice(0, 7)}`;
  }
  if (preset === "month") {
    return `month-${start.slice(0, 7)}`;
  }
  return `week-${start}`;
}

export function formatMonitoringTimestamp(value, { preset = "week", utcOffsetMinutes, detailed = false } = {}) {
  const parsed = parseTimelineValue(value);
  if (parsed === null) return "-";
  const date = shiftToOffset(new Date(parsed), resolveUtcOffsetMinutes(new Date(parsed), utcOffsetMinutes));
  if (preset === "months_6") {
    return formatShiftedDate(date, detailed ? { month: "short", year: "numeric" } : { month: "short" });
  }
  if (preset === "month") {
    return formatShiftedDate(date, detailed ? { month: "short", day: "numeric", year: "numeric" } : { month: "short", day: "numeric" });
  }
  return formatShiftedDate(date, detailed ? { month: "short", day: "numeric" } : { weekday: "short" });
}

export function formatMonitoringWindowLabel(window = {}) {
  const preset = String(window?.preset || "week").trim() || "week";
  const utcOffsetMinutes = resolveUtcOffsetMinutes(new Date(), window?.utcOffsetMinutes);
  if (preset === "month") {
    return formatMonitoringTimestamp(window?.startTime, {
      preset,
      utcOffsetMinutes,
      detailed: true,
    }).replace(/\s+\d{1,2},/, "");
  }
  if (preset === "months_6") {
    const startLabel = formatMonitoringTimestamp(window?.startTime, {
      preset,
      utcOffsetMinutes,
      detailed: true,
    });
    const endLabel = formatMonitoringTimestamp(window?.endTime, {
      preset,
      utcOffsetMinutes,
      detailed: true,
    });
    return `${startLabel} - ${endLabel}`;
  }
  const startLabel = formatMonitoringTimestamp(window?.startTime, {
    preset,
    utcOffsetMinutes,
    detailed: true,
  });
  const endLabel = formatMonitoringTimestamp(window?.endTime, {
    preset,
    utcOffsetMinutes,
    detailed: true,
  });
  return `${startLabel} - ${endLabel}`;
}

export function buildMonitoringTickLabels(samples = [], { preset = "week", utcOffsetMinutes } = {}) {
  return sortTimelineRows(Array.isArray(samples) ? samples : []).map((sample) => ({
    t: String(sample?.t || sample?.captured_at || sample?.bucket_start || "").trim(),
    label: formatMonitoringTimestamp(sample?.t || sample?.captured_at || sample?.bucket_start, {
      preset,
      utcOffsetMinutes,
      detailed: false,
    }),
  }));
}

function chartBucketStart(value, { preset = "week", utcOffsetMinutes } = {}) {
  const parsed = parseTimelineValue(value);
  if (parsed === null) return "";
  const sourceDate = new Date(parsed);
  const offset = resolveUtcOffsetMinutes(sourceDate, utcOffsetMinutes);
  const shifted = shiftToOffset(sourceDate, offset);
  const year = shifted.getUTCFullYear();
  const month = shifted.getUTCMonth();
  const day = shifted.getUTCDate();

  if (String(preset || "").trim() === "months_6") {
    return isoUtcSeconds(localBoundaryUtcDate(year, month, 1, 0, 0, 0, offset));
  }
  return isoUtcSeconds(localBoundaryUtcDate(year, month, day, 0, 0, 0, offset));
}

export function buildUsageChartSeries(samples = [], { preset = "week", utcOffsetMinutes } = {}) {
  const buckets = new Map();

  sortTimelineRows(Array.isArray(samples) ? samples : []).forEach((sample) => {
    const bucketKey = chartBucketStart(sample?.t || sample?.captured_at, { preset, utcOffsetMinutes });
    if (!bucketKey) return;
    const bucket = buckets.get(bucketKey) || {
      t: bucketKey,
      workers: 0,
      queued: 0,
      running: 0,
      completed: 0,
      failed: 0,
      retried: 0,
      mode: String(sample?.mode || "rest"),
    };
    bucket.workers = Math.max(Number(bucket.workers || 0), Number(sample?.workers || 0));
    bucket.queued = Math.max(Number(bucket.queued || 0), Number(sample?.queued || 0));
    bucket.running = Math.max(Number(bucket.running || 0), Number(sample?.running || 0));
    bucket.completed = Math.max(Number(bucket.completed || 0), Number(sample?.completed || 0));
    bucket.failed = Math.max(Number(bucket.failed || 0), Number(sample?.failed || 0));
    bucket.retried = Math.max(Number(bucket.retried || 0), Number(sample?.retried || 0));
    buckets.set(bucketKey, bucket);
  });

  return sortTimelineRows(Array.from(buckets.values()));
}

export function buildSpendChartSeries(samples = [], { preset = "week", utcOffsetMinutes } = {}) {
  const buckets = new Map();

  sortTimelineRows(Array.isArray(samples) ? samples : []).forEach((sample) => {
    const bucketKey = chartBucketStart(sample?.t || sample?.bucket_start, { preset, utcOffsetMinutes });
    if (!bucketKey) return;
    const bucket = buckets.get(bucketKey) || {
      t: bucketKey,
      cost: 0,
      records: 0,
      mode: String(sample?.mode || "rest"),
    };
    bucket.cost = Math.round((Number(bucket.cost || 0) + Number(sample?.cost || 0)) * 1_000_000) / 1_000_000;
    bucket.records = Number(bucket.records || 0) + Number(sample?.records || 0);
    buckets.set(bucketKey, bucket);
  });

  return sortTimelineRows(Array.from(buckets.values()));
}

export function buildSparseMonitoringTickLabels(samples = [], { preset = "week", utcOffsetMinutes, maxLabels = 6 } = {}) {
  const ticks = buildMonitoringTickLabels(samples, { preset, utcOffsetMinutes });
  if (!ticks.length) return [];
  if (ticks.length <= Math.max(Number(maxLabels) || 0, 2)) return ticks;

  const target = Math.max(Number(maxLabels) || 0, 2);
  const lastIndex = ticks.length - 1;
  const indexes = new Set([0, lastIndex]);
  for (let index = 1; index < target - 1; index += 1) {
    indexes.add(Math.round((lastIndex * index) / (target - 1)));
  }

  return [...indexes]
    .sort((left, right) => left - right)
    .map((index) => ticks[index])
    .filter((tick, index, items) => {
      if (!tick) return false;
      if (index === 0) return true;
      const previous = items[index - 1];
      return !previous || previous.label !== tick.label || previous.t !== tick.t;
    });
}

export function buildChartScaleLabels(maxValue, { integer = false } = {}) {
  const safeMax = Math.max(Number(maxValue) || 0, 0);
  if (!safeMax) return [0];
  const normalize = integer
    ? (value) => Math.max(0, Math.round(value))
    : (value) => Math.max(0, Math.round(value * 10) / 10);
  const top = integer ? Math.max(1, Math.ceil(safeMax)) : Math.round(safeMax * 100) / 100;
  const mid = normalize(top / 2);
  return [top, mid, 0].filter((value, index, items) => index === 0 || value !== items[index - 1]);
}

function toInteger(value, { field, allowEmpty = false } = {}) {
  if (value === "" || value === null || value === undefined) {
    if (allowEmpty) return undefined;
    throw new Error(`${field} is required.`);
  }
  const next = Number.parseInt(String(value), 10);
  if (!Number.isFinite(next)) {
    throw new Error(`${field} must be an integer.`);
  }
  return next;
}

export function prepareEndpointForm(endpoint) {
  const template = endpoint?.template || {};
  return {
    name: String(endpoint?.name || ""),
    gpuTypeIds: Array.isArray(endpoint?.gpu_types) ? endpoint.gpu_types.join(", ") : "",
    dataCenterIds: Array.isArray(endpoint?.data_center_ids) ? endpoint.data_center_ids.join(", ") : "",
    workersMin: endpoint?.workers_min ?? 0,
    workersMax: endpoint?.workers_max ?? 0,
    scalerType: String(endpoint?.scaler_type || ""),
    scalerValue: endpoint?.scaler_value ?? 0,
    idleTimeout: endpoint?.idle_timeout ?? 0,
    executionTimeoutMs: endpoint?.execution_timeout_ms ?? 0,
    flashBoot: Boolean(endpoint?.flash_boot),
    templateId: String(template?.id || ""),
    networkVolumeId: String(endpoint?.network_volume_id || ""),
  };
}

export function buildEndpointPatch(form) {
  const patch = {
    name: String(form?.name || "").trim(),
    gpuTypeIds: splitCsvList(form?.gpuTypeIds),
    dataCenterIds: splitCsvList(form?.dataCenterIds),
    workersMin: toInteger(form?.workersMin, { field: "workersMin" }),
    workersMax: toInteger(form?.workersMax, { field: "workersMax" }),
    scalerType: String(form?.scalerType || "").trim(),
    scalerValue: toInteger(form?.scalerValue, { field: "scalerValue" }),
    idleTimeout: toInteger(form?.idleTimeout, { field: "idleTimeout" }),
    executionTimeoutMs: toInteger(form?.executionTimeoutMs, { field: "executionTimeoutMs" }),
    flashBoot: Boolean(form?.flashBoot),
  };

  if (!patch.name) {
    delete patch.name;
  }
  if (!patch.gpuTypeIds.length) {
    throw new Error("gpuTypeIds must contain at least one GPU type.");
  }
  if (patch.workersMin < 0 || patch.workersMax < 0) {
    throw new Error("workersMin/workersMax must be 0 or greater.");
  }
  if (patch.workersMin > patch.workersMax) {
    throw new Error("workersMin must be less than or equal to workersMax.");
  }
  if (!patch.scalerType) {
    throw new Error("scalerType is required.");
  }
  if (patch.scalerValue < 0 || patch.idleTimeout < 0 || patch.executionTimeoutMs < 0) {
    throw new Error("Timeout and scaler values must be 0 or greater.");
  }

  const templateId = String(form?.templateId || "").trim();
  if (templateId) patch.templateId = templateId;

  const networkVolumeId = String(form?.networkVolumeId || "").trim();
  if (networkVolumeId) patch.networkVolumeId = networkVolumeId;

  return patch;
}

export function summarizeFleet(endpoints, billingRows = []) {
  const list = Array.isArray(endpoints) ? endpoints : [];
  const billing = Array.isArray(billingRows) ? billingRows : [];
  const gpuTypes = new Set();
  let managed = 0;
  let workersMin = 0;
  let workersMax = 0;
  let liveWorkers = 0;
  let queuedJobs = 0;
  let runningJobs = 0;

  list.forEach((endpoint) => {
    if (endpoint?.managed) managed += 1;
    if (Number.isFinite(endpoint?.workers_min)) workersMin += Number(endpoint.workers_min);
    if (Number.isFinite(endpoint?.workers_max)) workersMax += Number(endpoint.workers_max);
    if (Number.isFinite(endpoint?.worker_summary?.total)) liveWorkers += Number(endpoint.worker_summary.total);
    queuedJobs += Number(endpoint?.health_jobs?.in_queue || 0);
    runningJobs += Number(endpoint?.health_jobs?.in_progress || 0);
    (endpoint?.gpu_types || []).forEach((gpu) => gpuTypes.add(String(gpu)));
  });

  const totalCost = billing.reduce((sum, row) => sum + Number(row?.cost || 0), 0);

  return {
    totalEndpoints: list.length,
    managedEndpoints: managed,
    workersMin,
    workersMax,
    liveWorkers,
    queuedJobs,
    runningJobs,
    gpuTypeCount: gpuTypes.size,
    totalCost,
  };
}

function parseTimelineValue(value) {
  if (value === null || value === undefined || value === "") return null;
  const text = String(value).trim();
  if (!text) return null;
  const parsed = Date.parse(text);
  if (Number.isFinite(parsed)) return parsed;
  const asNumber = Number(text);
  return Number.isFinite(asNumber) ? asNumber : null;
}

function sortTimelineRows(rows = [], maxPoints = Infinity) {
  return [...rows]
    .sort((left, right) => {
      const leftTime = parseTimelineValue(left?.t);
      const rightTime = parseTimelineValue(right?.t);
      if (leftTime !== null && rightTime !== null && leftTime !== rightTime) {
        return leftTime - rightTime;
      }
      return String(left?.t || "").localeCompare(String(right?.t || ""));
    })
    .slice(-Math.max(Number(maxPoints) || rows.length || 1, 1));
}

function normalizeEndpointFilter(endpointIds) {
  const ids = Array.isArray(endpointIds)
    ? endpointIds.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  return ids.length ? new Set(ids) : null;
}

function csvCell(value) {
  const text = String(value ?? "");
  if (!/[",\n]/.test(text)) return text;
  return `"${text.replaceAll("\"", "\"\"")}"`;
}

export function deriveEndpointStatus(endpoint = {}) {
  const jobs = endpoint?.health_jobs || {};
  const states = endpoint?.worker_summary?.states || {};
  const runningJobs = Number(jobs?.in_progress || 0);
  const queuedJobs = Number(jobs?.in_queue || 0);
  const liveWorkers = Number(endpoint?.worker_summary?.total || 0);
  const workersMax = Number.isFinite(Number(endpoint?.workers_max)) ? Number(endpoint?.workers_max) : null;
  const warmWorkers = ["ready", "warm", "active"].reduce((sum, key) => sum + Number(states?.[key] || 0), 0);
  const idleWorkers = Number(states?.idle || 0);

  if (runningJobs > 0) {
    return { key: "running", label: "Running", tone: "running", count: runningJobs };
  }
  if (queuedJobs > 0) {
    return { key: "queued", label: "Queued", tone: "queued", count: queuedJobs };
  }
  if (workersMax === 0) {
    return { key: "paused", label: "Paused", tone: "paused", count: 0 };
  }
  if (warmWorkers > 0 || (liveWorkers > 0 && idleWorkers === 0)) {
    return { key: "warm", label: "Warm", tone: "warm", count: warmWorkers || liveWorkers };
  }
  return { key: "idle", label: "Idle", tone: "idle", count: idleWorkers || liveWorkers };
}


export function pickPreferredEndpointId(endpoints = [], managedServices = []) {
  const visibleIds = (Array.isArray(endpoints) ? endpoints : [])
    .map((item) => String(item?.id || "").trim())
    .filter(Boolean);
  for (const service of Array.isArray(managedServices) ? managedServices : []) {
    const endpointId = String(service?.endpoint_id || "").trim();
    if (endpointId && visibleIds.includes(endpointId)) {
      return endpointId;
    }
  }
  return visibleIds[0] || "";
}

export function buildEndpointScopeOptions(managedServices = [], endpoints = []) {
  const endpointNames = new Map(
    (Array.isArray(endpoints) ? endpoints : [])
      .map((endpoint) => [String(endpoint?.id || "").trim(), String(endpoint?.name || endpoint?.id || "").trim()])
      .filter(([endpointId]) => endpointId)
  );
  const seen = new Set();

  const scopes = [
    {
      key: "all",
      label: "All",
      endpointId: "all",
      available: true,
      subtitle: "Fleet view",
    },
  ];

  for (const service of Array.isArray(managedServices) ? managedServices : []) {
    const endpointId = String(service?.endpoint_id || "").trim();
    const fallbackKey = String(service?.key || service?.label || "").trim();
    const key = endpointId || fallbackKey;
    if (!key || seen.has(key)) continue;
    seen.add(key);

    const label = String(service?.label || service?.key || endpointId || "Service").trim() || "Service";
    const available = Boolean(endpointId && endpointNames.has(endpointId));
    scopes.push({
      key,
      label,
      endpointId: endpointId || key,
      available,
      subtitle: available ? endpointNames.get(endpointId) : "Not available",
    });
  }

  return scopes;
}


export function appendUsageHistory(historyByEndpoint = {}, endpoints = [], timestamp = Date.now(), maxPoints = 60) {
  const next = historyByEndpoint && typeof historyByEndpoint === "object" ? { ...historyByEndpoint } : {};
  const pointTime = Number(timestamp);
  const safeTimestamp = Number.isFinite(pointTime) ? pointTime : Date.now();

  (Array.isArray(endpoints) ? endpoints : []).forEach((endpoint) => {
    const endpointId = String(endpoint?.id || "").trim();
    if (!endpointId) return;

    const jobs = endpoint?.health_jobs || {};
    const sample = {
      t: safeTimestamp,
      workers: Number(endpoint?.worker_summary?.total || 0),
      queued: Number(jobs?.in_queue || 0),
      running: Number(jobs?.in_progress || 0),
    };

    const history = Array.isArray(next[endpointId]) ? next[endpointId].filter((item) => item && Number.isFinite(Number(item.t))) : [];
    const trimmed = history.length && Number(history[history.length - 1]?.t) === safeTimestamp ? history.slice(0, -1) : history;
    next[endpointId] = [...trimmed, sample].slice(-Math.max(Number(maxPoints) || 60, 1));
  });

  return next;
}


export function buildBillingSeries(records = [], endpointId = "", maxPoints = 14) {
  const targetId = String(endpointId || "").trim();
  const buckets = new Map();

  (Array.isArray(records) ? records : []).forEach((record) => {
    const rowEndpointId = String(record?.endpoint_id || record?.endpointId || "").trim();
    if (targetId && rowEndpointId !== targetId) return;

    const timestamp = String(record?.timestamp || record?.bucketStart || "").trim();
    if (!timestamp) return;

    const bucket = buckets.get(timestamp) || { t: timestamp, cost: 0, records: 0 };
    bucket.cost = Number(bucket.cost || 0) + Number(record?.cost || 0);
    bucket.records = Number(bucket.records || 0) + 1;
    buckets.set(timestamp, bucket);
  });

  return Array.from(buckets.values())
    .sort((left, right) => {
      const leftTime = Date.parse(String(left?.t || ""));
      const rightTime = Date.parse(String(right?.t || ""));
      if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) {
        return leftTime - rightTime;
      }
      return String(left?.t || "").localeCompare(String(right?.t || ""));
    })
    .slice(-Math.max(Number(maxPoints) || 14, 1))
    .map((bucket) => ({
      t: bucket.t,
      cost: Math.round(Number(bucket.cost || 0) * 1_000_000) / 1_000_000,
      records: Number(bucket.records || 0),
    }));
}

export function buildFleetBillingSeries(historyByEndpoint = {}, endpointIds = [], maxPoints = 30) {
  const filter = normalizeEndpointFilter(endpointIds);
  const buckets = new Map();

  Object.entries(historyByEndpoint || {}).forEach(([endpointId, samples]) => {
    if (filter && !filter.has(String(endpointId || "").trim())) return;
    (Array.isArray(samples) ? samples : []).forEach((sample) => {
      const timestamp = String(sample?.t || sample?.bucket_start || "").trim();
      if (!timestamp) return;
      const bucket = buckets.get(timestamp) || { t: timestamp, cost: 0, records: 0 };
      bucket.cost += Number(sample?.cost || 0);
      bucket.records += Number(sample?.records || 0);
      buckets.set(timestamp, bucket);
    });
  });

  return sortTimelineRows(Array.from(buckets.values()), maxPoints).map((bucket) => ({
    t: bucket.t,
    cost: Math.round(Number(bucket.cost || 0) * 1_000_000) / 1_000_000,
    records: Number(bucket.records || 0),
  }));
}

export function buildFleetUsageSeries(historyByEndpoint = {}, endpointIds = [], maxPoints = 120) {
  const filter = normalizeEndpointFilter(endpointIds);
  const buckets = new Map();

  Object.entries(historyByEndpoint || {}).forEach(([endpointId, samples]) => {
    if (filter && !filter.has(String(endpointId || "").trim())) return;
    (Array.isArray(samples) ? samples : []).forEach((sample) => {
      const timestamp = String(sample?.t || sample?.captured_at || "").trim();
      if (!timestamp) return;
      const bucket = buckets.get(timestamp) || { t: timestamp, workers: 0, queued: 0, running: 0 };
      bucket.workers += Number(sample?.workers || 0);
      bucket.queued += Number(sample?.queued || 0);
      bucket.running += Number(sample?.running || 0);
      buckets.set(timestamp, bucket);
    });
  });

  return sortTimelineRows(Array.from(buckets.values()), maxPoints).map((bucket) => ({
    t: bucket.t,
    workers: Number(bucket.workers || 0),
    queued: Number(bucket.queued || 0),
    running: Number(bucket.running || 0),
  }));
}

export function buildBillingHistoryRows(historyByEndpoint = {}, endpointIds = [], endpointNames = {}) {
  const filter = normalizeEndpointFilter(endpointIds);
  const rows = [];

  Object.entries(historyByEndpoint || {})
    .filter(([endpointId]) => !filter || filter.has(String(endpointId || "").trim()))
    .sort(([leftId], [rightId]) => String(leftId || "").localeCompare(String(rightId || "")))
    .forEach(([endpointId, samples]) => {
      sortTimelineRows(Array.isArray(samples) ? samples : []).forEach((sample) => {
        rows.push({
          endpoint_id: endpointId,
          endpoint_name: String(endpointNames?.[endpointId] || "").trim(),
          timestamp: String(sample?.t || sample?.bucket_start || "").trim(),
          cost: Number(sample?.cost || 0),
          records: Number(sample?.records || 0),
        });
      });
    });

  return rows;
}

export function buildBillingCsv(records = [], endpointNames = {}) {
  const lines = ["endpoint_id,endpoint_name,timestamp,cost,records"];
  const rows = [...(Array.isArray(records) ? records : [])].sort((left, right) => {
    const leftEndpoint = String(left?.endpoint_id || left?.endpointId || "");
    const rightEndpoint = String(right?.endpoint_id || right?.endpointId || "");
    if (leftEndpoint !== rightEndpoint) return leftEndpoint.localeCompare(rightEndpoint);
    const leftTime = String(left?.timestamp || left?.bucketStart || left?.t || "");
    const rightTime = String(right?.timestamp || right?.bucketStart || right?.t || "");
    return leftTime.localeCompare(rightTime);
  });

  rows.forEach((row) => {
    const endpointId = String(row?.endpoint_id || row?.endpointId || "").trim();
    const endpointName = String(row?.endpoint_name || endpointNames?.[endpointId] || "").trim();
    const timestamp = String(row?.timestamp || row?.bucketStart || row?.t || "").trim();
    const cost = Number(row?.cost || 0);
    const recordsCount = Number(row?.records || 0);
    lines.push([
      csvCell(endpointId),
      csvCell(endpointName),
      csvCell(timestamp),
      csvCell(Number.isFinite(cost) ? cost : 0),
      csvCell(Number.isFinite(recordsCount) ? recordsCount : 0),
    ].join(","));
  });

  return lines.join("\n");
}

export function buildUsageCsv(historyByEndpoint = {}, endpointIds = []) {
  const filter = normalizeEndpointFilter(endpointIds);
  const lines = ["endpoint_id,timestamp,workers,queued,running"];

  Object.entries(historyByEndpoint || {})
    .filter(([endpointId]) => !filter || filter.has(String(endpointId || "").trim()))
    .sort(([leftId], [rightId]) => String(leftId || "").localeCompare(String(rightId || "")))
    .forEach(([endpointId, samples]) => {
      sortTimelineRows(Array.isArray(samples) ? samples : []).forEach((sample) => {
        lines.push([
          csvCell(endpointId),
          csvCell(String(sample?.t || sample?.captured_at || "").trim()),
          csvCell(Number(sample?.workers || 0)),
          csvCell(Number(sample?.queued || 0)),
          csvCell(Number(sample?.running || 0)),
        ].join(","));
      });
    });

  return lines.join("\n");
}
