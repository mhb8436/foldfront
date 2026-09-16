import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  appendUsageHistory,
  buildBillingCsv,
  buildBillingHistoryRows,
  buildBillingSeries,
  buildChartScaleLabels,
  buildFleetBillingSeries,
  buildFleetUsageSeries,
  buildPreviousMonitoringWindow,
  buildSparseMonitoringTickLabels,
  buildMonitoringTickLabels,
  buildMonitoringWindow,
  buildUsageChartSeries,
  buildEndpointPatch,
  canNavigateToNextMonitoringWindow,
  shouldScrollDetailIntoView,
  summarizeMonitoringComparison,
  shiftMonitoringWindow,
  buildUsageCsv,
  buildWindowDownloadSuffix,
  deriveEndpointStatus,
  formatSpendSummary,
  normalizeApiBase,
  pickPreferredEndpointId,
  prepareEndpointForm,
  resolveDefaultApiBase,
  summarizeFleet,
} from "../runpod-admin/lib.js";

test("normalizeApiBase trims and removes trailing slash", () => {
  assert.equal(normalizeApiBase(" http://127.0.0.1:18080/ "), "http://127.0.0.1:18080");
});

test("resolveDefaultApiBase prefers proxied pipeline path", () => {
  assert.equal(
    resolveDefaultApiBase({ origin: "https://example.org", pathname: "/pipeline/runpod-admin/" }),
    "https://example.org/pipeline/api"
  );
});

test("prepareEndpointForm flattens endpoint data for editing", () => {
  const form = prepareEndpointForm({
    name: "MMseqs Production",
    gpu_types: ["NVIDIA A100 80GB PCIe"],
    data_center_ids: ["US-KS-2"],
    workers_min: 1,
    workers_max: 4,
    scaler_type: "QUEUE_DELAY",
    scaler_value: 3,
    idle_timeout: 5,
    execution_timeout_ms: 600000,
    flash_boot: true,
    template: { id: "tmpl-1" },
    network_volume_id: "nv-1",
  });
  assert.equal(form.gpuTypeIds, "NVIDIA A100 80GB PCIe");
  assert.equal(form.templateId, "tmpl-1");
  assert.equal(form.networkVolumeId, "nv-1");
});

test("buildEndpointPatch validates and converts fields", () => {
  const patch = buildEndpointPatch({
    name: "MMseqs Production",
    gpuTypeIds: "NVIDIA A100 80GB PCIe, NVIDIA H100 PCIe",
    dataCenterIds: "US-KS-2, EU-CZ-1",
    workersMin: "1",
    workersMax: "4",
    scalerType: "QUEUE_DELAY",
    scalerValue: "2",
    idleTimeout: "5",
    executionTimeoutMs: "600000",
    flashBoot: true,
  });
  assert.deepEqual(patch.gpuTypeIds, ["NVIDIA A100 80GB PCIe", "NVIDIA H100 PCIe"]);
  assert.deepEqual(patch.dataCenterIds, ["US-KS-2", "EU-CZ-1"]);
  assert.equal(patch.workersMax, 4);
});

test("summarizeFleet aggregates scaling and billing totals", () => {
  const summary = summarizeFleet(
    [
      {
        managed: true,
        workers_min: 1,
        workers_max: 4,
        worker_summary: { total: 1 },
        gpu_types: ["A100"],
      },
      {
        managed: false,
        workers_min: 0,
        workers_max: 2,
        worker_summary: { total: 0 },
        gpu_types: ["L40S"],
      },
    ],
    [
      { endpoint_id: "ep-managed", cost: 12.5 },
      { endpoint_id: "ep-other", cost: 2.0 },
    ]
  );
  assert.equal(summary.totalEndpoints, 2);
  assert.equal(summary.managedEndpoints, 1);
  assert.equal(summary.workersMax, 6);
  assert.equal(summary.gpuTypeCount, 2);
  assert.equal(summary.totalCost, 14.5);
});


test("formatSpendSummary distinguishes unavailable billing from real zero", () => {
  assert.equal(formatSpendSummary(0, "unavailable"), "Unavailable");
  assert.equal(formatSpendSummary(0, "rest"), "$0.00");
});


test("pickPreferredEndpointId prefers managed service order", () => {
  const picked = pickPreferredEndpointId(
    [{ id: "ep-af2" }, { id: "ep-mmseqs" }, { id: "ep-rfd3" }],
    [
      { endpoint_id: "ep-mmseqs" },
      { endpoint_id: "ep-af2" },
    ]
  );
  assert.equal(picked, "ep-mmseqs");
});


test("appendUsageHistory appends capped samples per endpoint", () => {
  const history = appendUsageHistory(
    {
      "ep-1": [{ t: 1, workers: 1, queued: 0, running: 0 }],
    },
    [
      { id: "ep-1", worker_summary: { total: 3 }, health_jobs: { in_queue: 2, in_progress: 1 } },
      { id: "ep-2", worker_summary: { total: 5 }, health_jobs: { in_queue: 0, in_progress: 4 } },
    ],
    2,
    2
  );
  assert.equal(history["ep-1"].length, 2);
  assert.deepEqual(history["ep-1"][1], { t: 2, workers: 3, queued: 2, running: 1 });
  assert.deepEqual(history["ep-2"][0], { t: 2, workers: 5, queued: 0, running: 4 });
});


test("buildBillingSeries groups cost buckets by endpoint", () => {
  const series = buildBillingSeries(
    [
      { endpoint_id: "ep-1", timestamp: "2026-03-07T00:00:00Z", cost: 1.25 },
      { endpoint_id: "ep-2", timestamp: "2026-03-07T00:00:00Z", cost: 9.0 },
      { endpoint_id: "ep-1", timestamp: "2026-03-07T00:00:00Z", cost: 0.5 },
      { endpoint_id: "ep-1", timestamp: "2026-03-08T00:00:00Z", cost: 2.0 },
    ],
    "ep-1",
    7
  );

  assert.deepEqual(series, [
    { t: "2026-03-07T00:00:00Z", cost: 1.75, records: 2 },
    { t: "2026-03-08T00:00:00Z", cost: 2, records: 1 },
  ]);
});

test("deriveEndpointStatus separates queued, running, warm, idle, and paused states", () => {
  assert.equal(
    deriveEndpointStatus({
      workers_max: 4,
      worker_summary: { total: 4, states: { idle: 4 } },
      health_jobs: { in_queue: 3, in_progress: 0 },
    }).label,
    "Queued"
  );
  assert.equal(
    deriveEndpointStatus({
      workers_max: 4,
      worker_summary: { total: 4, states: { running: 1, idle: 3 } },
      health_jobs: { in_queue: 0, in_progress: 1 },
    }).label,
    "Running"
  );
  assert.equal(
    deriveEndpointStatus({
      workers_max: 4,
      worker_summary: { total: 2, states: { ready: 2 } },
      health_jobs: { in_queue: 0, in_progress: 0 },
    }).label,
    "Warm"
  );
  assert.equal(
    deriveEndpointStatus({
      workers_max: 4,
      worker_summary: { total: 0, states: {} },
      health_jobs: { in_queue: 0, in_progress: 0 },
    }).label,
    "Idle"
  );
  assert.equal(
    deriveEndpointStatus({
      workers_max: 0,
      worker_summary: { total: 0, states: {} },
      health_jobs: { in_queue: 0, in_progress: 0 },
    }).label,
    "Paused"
  );
});

test("buildFleetBillingSeries aggregates matching time buckets across endpoints", () => {
  const series = buildFleetBillingSeries({
    "ep-1": [
      { t: "2026-03-07T00:00:00Z", cost: 1.25, records: 1 },
      { t: "2026-03-08T00:00:00Z", cost: 2.0, records: 1 },
    ],
    "ep-2": [
      { t: "2026-03-07T00:00:00Z", cost: 0.75, records: 1 },
      { t: "2026-03-08T00:00:00Z", cost: 1.0, records: 1 },
    ],
  });

  assert.deepEqual(series, [
    { t: "2026-03-07T00:00:00Z", cost: 2, records: 2 },
    { t: "2026-03-08T00:00:00Z", cost: 3, records: 2 },
  ]);
});

test("buildFleetUsageSeries aggregates workers, queued, and running by time bucket", () => {
  const series = buildFleetUsageSeries({
    "ep-1": [
      { t: "2026-03-07T00:00:00Z", workers: 2, queued: 0, running: 0 },
      { t: "2026-03-08T00:00:00Z", workers: 2, queued: 1, running: 1 },
    ],
    "ep-2": [
      { t: "2026-03-07T00:00:00Z", workers: 1, queued: 2, running: 0 },
      { t: "2026-03-08T00:00:00Z", workers: 0, queued: 0, running: 0 },
    ],
  });

  assert.deepEqual(series, [
    { t: "2026-03-07T00:00:00Z", workers: 3, queued: 2, running: 0 },
    { t: "2026-03-08T00:00:00Z", workers: 2, queued: 1, running: 1 },
  ]);
});

test("buildBillingCsv serializes endpoint billing rows with stable headers", () => {
  const csv = buildBillingCsv([
    { endpoint_id: "ep-1", endpoint_name: "MMseqs", timestamp: "2026-03-07T00:00:00Z", cost: 1.25, records: 2 },
  ]);

  assert.equal(
    csv,
    "endpoint_id,endpoint_name,timestamp,cost,records\nep-1,MMseqs,2026-03-07T00:00:00Z,1.25,2"
  );
});

test("buildUsageCsv serializes endpoint usage rows with stable headers", () => {
  const csv = buildUsageCsv({
    "ep-1": [
      { t: "2026-03-07T00:00:00Z", workers: 2, queued: 1, running: 0 },
      { t: "2026-03-08T00:00:00Z", workers: 2, queued: 0, running: 1 },
    ],
  });

  assert.equal(
    csv,
    "endpoint_id,timestamp,workers,queued,running\nep-1,2026-03-07T00:00:00Z,2,1,0\nep-1,2026-03-08T00:00:00Z,2,0,1"
  );
});

test("buildMonitoringWindow aligns week preset to Monday-Sunday boundaries", () => {
  const window = buildMonitoringWindow("week", {
    now: new Date("2026-03-11T12:00:00Z"),
    utcOffsetMinutes: 540,
  });

  assert.equal(window.startTime, "2026-03-08T15:00:00Z");
  assert.equal(window.endTime, "2026-03-15T14:59:59Z");
  assert.equal(window.usageResolution, "day");
  assert.equal(window.billingResolution, "day");
});

test("buildMonitoringWindow aligns multi-month preset to calendar month boundaries", () => {
  const window = buildMonitoringWindow("months_6", {
    now: new Date("2026-03-11T12:00:00Z"),
    utcOffsetMinutes: 540,
  });

  assert.equal(window.startTime, "2025-09-30T15:00:00Z");
  assert.equal(window.endTime, "2026-03-31T14:59:59Z");
  assert.equal(window.usageResolution, "month");
  assert.equal(window.billingResolution, "month");
});

test("buildMonitoringWindow supports single calendar month boundaries", () => {
  const window = buildMonitoringWindow("month", {
    now: new Date("2026-03-11T12:00:00Z"),
    utcOffsetMinutes: 540,
  });

  assert.equal(window.startTime, "2026-02-28T15:00:00Z");
  assert.equal(window.endTime, "2026-03-31T14:59:59Z");
  assert.equal(window.usageResolution, "day");
  assert.equal(window.billingResolution, "day");
});

test("shiftMonitoringWindow moves weekly windows backward and forward by one full period", () => {
  const current = buildMonitoringWindow("week", {
    now: new Date("2026-03-11T12:00:00Z"),
    utcOffsetMinutes: 540,
  });

  const previous = shiftMonitoringWindow(current, -1);
  assert.equal(previous.startTime, "2026-03-01T15:00:00Z");
  assert.equal(previous.endTime, "2026-03-08T14:59:59Z");

  const next = shiftMonitoringWindow(previous, 1);
  assert.equal(next.startTime, current.startTime);
  assert.equal(next.endTime, current.endTime);
});

test("buildPreviousMonitoringWindow derives the immediately previous matching calendar period", () => {
  const current = buildMonitoringWindow("month", {
    now: new Date("2026-03-11T12:00:00Z"),
    utcOffsetMinutes: 540,
  });
  const previous = buildPreviousMonitoringWindow(current);

  assert.equal(previous.startTime, "2026-01-31T15:00:00Z");
  assert.equal(previous.endTime, "2026-02-28T14:59:59Z");
  assert.equal(previous.preset, "month");
});

test("canNavigateToNextMonitoringWindow prevents moving beyond the current calendar period", () => {
  const current = buildMonitoringWindow("month", {
    now: new Date("2026-03-11T12:00:00Z"),
    utcOffsetMinutes: 540,
  });
  const previous = buildPreviousMonitoringWindow(current);

  assert.equal(canNavigateToNextMonitoringWindow(current, { now: new Date("2026-03-11T12:00:00Z"), utcOffsetMinutes: 540 }), false);
  assert.equal(canNavigateToNextMonitoringWindow(previous, { now: new Date("2026-03-11T12:00:00Z"), utcOffsetMinutes: 540 }), true);
});

test("buildMonitoringTickLabels emits weekdays for week and month names for multi-month views", () => {
  const weekLabels = buildMonitoringTickLabels(
    [
      { t: "2026-03-08T15:00:00Z" },
      { t: "2026-03-09T15:00:00Z" },
      { t: "2026-03-10T15:00:00Z" },
    ],
    { preset: "week", utcOffsetMinutes: 540 }
  );
  assert.deepEqual(
    weekLabels.map((item) => item.label),
    ["Mon", "Tue", "Wed"]
  );

  const monthLabels = buildMonitoringTickLabels(
    [
      { t: "2025-09-30T15:00:00Z" },
      { t: "2025-10-31T15:00:00Z" },
      { t: "2025-11-30T15:00:00Z" },
    ],
    { preset: "months_6", utcOffsetMinutes: 540 }
  );
  assert.deepEqual(
    monthLabels.map((item) => item.label),
    ["Oct", "Nov", "Dec"]
  );
});

test("buildUsageChartSeries rolls repeated same-day samples into one daily bucket for month view", () => {
  const series = buildUsageChartSeries(
    [
      { t: "2026-03-11T01:00:00Z", workers: 1, queued: 0, running: 0, mode: "rest" },
      { t: "2026-03-11T18:00:00Z", workers: 3, queued: 2, running: 1, mode: "rest" },
      { t: "2026-03-12T03:00:00Z", workers: 2, queued: 0, running: 0, mode: "rest" },
    ],
    { preset: "month", utcOffsetMinutes: 0 }
  );

  assert.deepEqual(series, [
    {
      t: "2026-03-11T00:00:00Z",
      workers: 3,
      queued: 2,
      running: 1,
      completed: 0,
      failed: 0,
      retried: 0,
      mode: "rest",
    },
    {
      t: "2026-03-12T00:00:00Z",
      workers: 2,
      queued: 0,
      running: 0,
      completed: 0,
      failed: 0,
      retried: 0,
      mode: "rest",
    },
  ]);
});

test("buildSparseMonitoringTickLabels caps label count and keeps first and last points", () => {
  const samples = Array.from({ length: 10 }, (_, index) => ({
    t: `2026-03-${String(index + 1).padStart(2, "0")}T00:00:00Z`,
  }));

  const labels = buildSparseMonitoringTickLabels(samples, {
    preset: "month",
    utcOffsetMinutes: 0,
    maxLabels: 5,
  });

  assert.equal(labels.length, 5);
  assert.equal(labels[0].label, "Mar 1");
  assert.equal(labels.at(-1)?.label, "Mar 10");
});

test("buildChartScaleLabels exposes top midpoint and zero markers", () => {
  assert.deepEqual(buildChartScaleLabels(6, { integer: true }), [6, 3, 0]);
});

test("buildWindowDownloadSuffix uses calendar preset naming instead of trailing day counts", () => {
  assert.equal(
    buildWindowDownloadSuffix({
      preset: "week",
      startTime: "2026-03-08T15:00:00Z",
      endTime: "2026-03-15T14:59:59Z",
      utcOffsetMinutes: 540,
    }),
    "week-2026-03-09"
  );
  assert.equal(
    buildWindowDownloadSuffix({
      preset: "months_6",
      startTime: "2025-09-30T15:00:00Z",
      endTime: "2026-03-31T14:59:59Z",
      utcOffsetMinutes: 540,
    }),
    "months-6-2025-10-to-2026-03"
  );
});

test("buildBillingHistoryRows flattens aggregated billing history for calendar exports", () => {
  const rows = buildBillingHistoryRows(
    {
      "ep-1": [
        { t: "2026-03-01T00:00:00Z", cost: 3.5, records: 2 },
        { t: "2026-04-01T00:00:00Z", cost: 1.25, records: 1 },
      ],
      "ep-2": [{ t: "2026-03-01T00:00:00Z", cost: 9.0, records: 1 }],
    },
    ["ep-1"],
    { "ep-1": "MMseqs" }
  );

  assert.deepEqual(rows, [
    {
      endpoint_id: "ep-1",
      endpoint_name: "MMseqs",
      timestamp: "2026-03-01T00:00:00Z",
      cost: 3.5,
      records: 2,
    },
    {
      endpoint_id: "ep-1",
      endpoint_name: "MMseqs",
      timestamp: "2026-04-01T00:00:00Z",
      cost: 1.25,
      records: 1,
    },
  ]);
});

test("runpod admin markup exposes previous-next navigation controls and a month preset", () => {
  const html = readFileSync(new URL("../runpod-admin/index.html", import.meta.url), "utf8");

  assert.match(html, /id="periodNavPrevBtn"/);
  assert.match(html, /id="periodNavNextBtn"/);
  assert.match(html, /id="periodLabel"/);
  assert.match(html, /option value="month"/);
});

test("runpod admin markup uses a compact header with settings disclosure", () => {
  const html = readFileSync(new URL("../runpod-admin/index.html", import.meta.url), "utf8");
  assert.doesNotMatch(html, /RunPod Operations Deck/);
  assert.match(html, /id="settingsPanel"/);
  assert.match(html, /Pipeline API Base/);
  assert.match(html, /RunPod Ops/);
});

test("runpod admin styles keep the title on one line instead of a large hero block", () => {
  const css = readFileSync(new URL("../runpod-admin/styles.css", import.meta.url), "utf8");
  assert.match(css, /\.app-title\s*\{[^}]*white-space:\s*nowrap;/s);
});

test("runpod admin markup removes the duplicate managed endpoint snapshot block", () => {
  const html = readFileSync(new URL("../runpod-admin/index.html", import.meta.url), "utf8");
  assert.doesNotMatch(html, /Managed endpoint snapshot/);
});

test("runpod admin markup replaces the split watchboard with a unified endpoint area", () => {
  const html = readFileSync(new URL("../runpod-admin/index.html", import.meta.url), "utf8");
  assert.doesNotMatch(html, /Serverless watchboard/);
  assert.match(html, /id="endpointScopeSelector"/);
  assert.match(html, /id="endpointWorkspace"/);
});

test("runpod admin markup reduces the top summary strip to five cards", () => {
  const html = readFileSync(new URL("../runpod-admin/index.html", import.meta.url), "utf8");
  const count = (html.match(/class="summary-card/g) || []).length;
  assert.equal(count, 5);
});

test("runpod admin styles do not trap the detail panel in an internal scroll container", () => {
  const css = readFileSync(new URL("../runpod-admin/styles.css", import.meta.url), "utf8");
  assert.doesNotMatch(css, /\.detail-panel\s*\{[^}]*overflow:\s*auto;/s);
});

test("runpod admin styles expose chart axis and stat rows for readability", () => {
  const css = readFileSync(new URL("../runpod-admin/styles.css", import.meta.url), "utf8");
  assert.match(css, /\.chart-frame\s*\{/);
  assert.match(css, /\.chart-y-axis\s*\{/);
  assert.match(css, /\.chart-stat-grid\s*\{/);
});

test("shouldScrollDetailIntoView scrolls on narrow screens but not desktop", () => {
  assert.equal(shouldScrollDetailIntoView(1440), false);
  assert.equal(shouldScrollDetailIntoView(720), true);
});

test("buildEndpointScopeOptions prepends All and flags missing managed mappings", async () => {
  const { buildEndpointScopeOptions } = await import("../runpod-admin/lib.js");
  const scopes = buildEndpointScopeOptions(
    [
      { label: "MMseqs", endpoint_id: "ep-1", configured: true },
      { label: "AF2", endpoint_id: "ep-missing", configured: true },
    ],
    [{ id: "ep-1", name: "MMseqs Prod" }]
  );

  assert.deepEqual(scopes, [
    { key: "all", label: "All", endpointId: "all", available: true, subtitle: "Fleet view" },
    { key: "ep-1", label: "MMseqs", endpointId: "ep-1", available: true, subtitle: "MMseqs Prod" },
    { key: "ep-missing", label: "AF2", endpointId: "ep-missing", available: false, subtitle: "Not available" },
  ]);
});

test("summarizeMonitoringComparison computes current, previous, and delta metrics", () => {
  const comparison = summarizeMonitoringComparison({
    currentUsage: [
      { workers: 4, running: 2, queued: 1 },
      { workers: 2, running: 1, queued: 3 },
    ],
    previousUsage: [
      { workers: 2, running: 1, queued: 1 },
      { workers: 2, running: 1, queued: 2 },
    ],
    currentBilling: [
      { cost: 4.0 },
      { cost: 5.0 },
    ],
    previousBilling: [
      { cost: 2.0 },
      { cost: 4.0 },
    ],
  });

  assert.deepEqual(comparison, {
    spend: { current: 9, previous: 6, delta: 3, deltaPct: 50 },
    avgWorkers: { current: 3, previous: 2, delta: 1, deltaPct: 50 },
    avgRunning: { current: 1.5, previous: 1, delta: 0.5, deltaPct: 50 },
    peakQueued: { current: 3, previous: 2, delta: 1, deltaPct: 50 },
  });
});
