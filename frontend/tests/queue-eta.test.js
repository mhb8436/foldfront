import test from "node:test";
import assert from "node:assert/strict";

import { etaMinutes, finishClock, renderQueueEta } from "../lib/queue-eta.js";

test("etaMinutes ceilings to minutes, min 1, null for none", () => {
  assert.equal(etaMinutes(120), 2);
  assert.equal(etaMinutes(30), 1);
  assert.equal(etaMinutes(0), null);
  assert.equal(etaMinutes(null), null);
});

test("finishClock returns HH:MM and null for missing", () => {
  assert.match(finishClock(3600, Date.UTC(2026, 0, 1, 0, 0, 0)), /^\d{2}:\d{2}$/);
  assert.equal(finishClock(null), null);
});

test("renderQueueEta normal shows stage, jobs ahead, minutes, approx", () => {
  const html = renderQueueEta(
    { est_finish_s: 120, fallback: false, current_stage: "af2",
      per_stage: [{ stage: "af2", queued: 3, running: 1 }] },
    "ko",
  );
  assert.match(html, /af2/);
  assert.match(html, /3/);
  assert.match(html, /2분/);
  assert.match(html, /근사/);
});

test("renderQueueEta fallback with health shows counts, no time", () => {
  const html = renderQueueEta(
    { est_finish_s: null, fallback: true,
      per_stage: [{ stage: "msa", queued: 5, running: 0 }] },
    "ko",
  );
  assert.match(html, /대기 5/);
  assert.match(html, /준비 중/);
});

test("renderQueueEta with no health data shows collecting, not zero", () => {
  const html = renderQueueEta(
    { est_finish_s: null, fallback: true,
      per_stage: [{ stage: "msa", queued: null, running: null }] },
    "ko",
  );
  assert.match(html, /수집 중/);
  assert.doesNotMatch(html, /대기 0/);
});

test("renderQueueEta handles empty payload", () => {
  assert.equal(renderQueueEta(null), "");
});
