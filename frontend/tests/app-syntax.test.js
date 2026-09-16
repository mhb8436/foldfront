import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildCopilotReply } from "../lib/copilot.js";

test("frontend app source parses as an ES module", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const tempDir = mkdtempSync(join(tmpdir(), "kbf-app-check-"));
  const tempFile = join(tempDir, "app-check.mjs");
  writeFileSync(tempFile, source, "utf8");

  let output = "";
  try {
    execFileSync(process.execPath, ["--check", tempFile], {
      encoding: "utf8",
      stdio: "pipe",
    });
  } catch (error) {
    output = `${error.stdout || ""}${error.stderr || ""}`.trim();
  }

  assert.equal(output, "");
});

test("managed background jobs stay in CATH ops instead of the run monitor", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.doesNotMatch(html, /id="monitorManagedJobsSection"/);
  assert.doesNotMatch(html, /id="monitorManagedJobsList"/);
  assert.match(source, /pipeline\.cath_list_jobs/);
  assert.match(source, /data-cath-job-delete/);
  assert.match(source, /pipeline\.cath_delete_job/);
});

test("pipeline route defaults do not silently force RFD3 on", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.doesNotMatch(
    source,
    /if \(mode === "pipeline"\) return \{[^}]*rfd3_use:\s*true[^}]*\}/
  );
});

test("non-production hosts get visible environment badges", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  assert.match(html, /id="environmentBadge"/);
  assert.match(source, /dev-pipeline\.k-biofoundrycopilot\.duckdns\.org/);
  assert.match(source, /staging-pipeline\.k-biofoundrycopilot\.duckdns\.org/);
  assert.match(source, /body\.dataset\.environment/);
  assert.match(styles, /body\[data-environment="development"\]/);
  assert.match(styles, /body\[data-environment="staging"\]/);
});

test("frontend has Vite and Tailwind build wiring", () => {
  const packageJson = JSON.parse(
    readFileSync(new URL("../package.json", import.meta.url), "utf8")
  );
  const viteConfig = readFileSync(
    new URL("../vite.config.mjs", import.meta.url),
    "utf8"
  );
  const tailwindEntry = readFileSync(
    new URL("../tailwind-entry.css", import.meta.url),
    "utf8"
  );

  assert.equal(packageJson.scripts.build, "vite build");
  assert.equal(packageJson.scripts.dev, "vite --host 127.0.0.1");
  assert.ok(packageJson.devDependencies.vite);
  assert.ok(packageJson.devDependencies.tailwindcss);
  assert.ok(packageJson.devDependencies["@tailwindcss/vite"]);
  assert.match(viteConfig, /@tailwindcss\/vite/);
  assert.match(viteConfig, /tailwindcss\(\)/);
  assert.match(tailwindEntry, /@import "tailwindcss"/);
});

test("frontend build runs in deployment checks and local deploy", () => {
  const workflow = readFileSync(
    new URL("../../.github/workflows/deploy.yml", import.meta.url),
    "utf8"
  );
  const deployScript = readFileSync(
    new URL("../../scripts/deploy/deploy_from_github.sh", import.meta.url),
    "utf8"
  );

  assert.match(workflow, /npm --prefix frontend ci/);
  assert.match(workflow, /npm --prefix frontend run build/);
  assert.match(deployScript, /npm --prefix frontend ci/);
  assert.match(deployScript, /npm --prefix frontend run build/);
});

test("advanced setup uses a staged wizard with a final review step", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(source, /const ENABLE_SETUP_WIZARD = true/);
  assert.match(source, /setup\.wizard\.expert/);
  assert.match(source, /setup\.wizard\.review/);
  assert.match(source, /function renderSetupReviewCard/);
});

test("advanced setup visual hierarchy uses compact review and expert styling", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  assert.doesNotMatch(styles, /\.bg-orb/);
  assert.match(styles, /--radius-sm:\s*8px/);
  assert.match(styles, /\.setup-review-card/);
  assert.match(styles, /\.expert-option/);
  assert.match(html, /class="setup-ux-grid setup-primary-layout"/);
});

test("advanced optional prompt is collapsed and layout width is fluid", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(html, /<details[^>]+id="advancedPromptDetails"[^>]*class="advanced-prompt-details"[\s\S]*id="promptInput"[\s\S]*<\/details>/);
  assert.match(html, /class="composer-actions advanced-primary-actions"[\s\S]*id="checkBtn"[\s\S]*id="planBtn"/);
  assert.doesNotMatch(html, /<textarea\s+id="promptInput"[\s\S]*class="composer-actions advanced-primary-actions"/);
  assert.match(source, /"setup\.prompt\.summary": "Optional notes or natural-language setup"/);
  assert.match(source, /"setup\.prompt\.summary": "선택 메모 또는 자연어 설정"/);
  assert.match(styles, /\.setup-primary-layout\s*\{[\s\S]*--setup-content-max:\s*clamp\(960px,\s*86vw,\s*1320px\);/m);
  assert.match(styles, /\.setup-primary-layout\[data-active-step="input"\]\s*\{[\s\S]*grid-template-columns:\s*minmax\(0,\s*min\(100%,\s*var\(--setup-content-max\)\)\);/m);
});

test("all primary tabs share fluid shell width and responsive grid rules", () => {
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  assert.match(styles, /--content-max:\s*1680px;/);
  assert.match(styles, /--content-gutter:\s*clamp\(16px,\s*2\.2vw,\s*40px\);/);
  assert.match(styles, /--layout-gap:\s*clamp\(16px,\s*1\.6vw,\s*28px\);/);
  assert.match(styles, /\.topbar\s*\{[\s\S]*max-width:\s*min\(100%,\s*var\(--content-max\)\);[\s\S]*padding:\s*30px\s+var\(--content-gutter\)\s+18px;/m);
  assert.match(styles, /\.layout\s*\{[\s\S]*padding:\s*0\s+var\(--content-gutter\)\s+clamp\(28px,\s*3vw,\s*44px\);/m);
  assert.match(styles, /\.app-shell\s*\{[\s\S]*max-width:\s*min\(100%,\s*var\(--content-max\)\);[\s\S]*margin:\s*0\s+auto;/m);
  assert.match(styles, /\.tab-content-shell\s*\{[\s\S]*max-width:\s*min\(100%,\s*var\(--content-max\)\);[\s\S]*padding:\s*var\(--tab-shell-padding\);/m);
  assert.doesNotMatch(styles, /\.tab-panel\[data-tab="advanced"\]\s+\.tab-content-shell\s*\{[\s\S]*max-width:/m);
  assert.match(styles, /\.home-shell\s*\{[\s\S]*gap:\s*var\(--layout-gap\);/m);
  assert.match(styles, /\.experiment-launchpad\s*\{[\s\S]*grid-template-columns:\s*minmax\(0,\s*1\.18fr\)\s+minmax\(clamp\(280px,\s*24vw,\s*420px\),\s*0\.82fr\);[\s\S]*gap:\s*var\(--layout-gap\);/m);
  assert.match(styles, /\.fast-grid,\s*\.evolution-grid\s*\{[\s\S]*gap:\s*var\(--layout-gap\);/m);
  assert.match(styles, /\.monitor-grid\s*\{[\s\S]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);[\s\S]*gap:\s*var\(--layout-gap\);/m);
  assert.match(styles, /\.studio-grid\s*\{[\s\S]*grid-template-columns:\s*minmax\(clamp\(220px,\s*18vw,\s*300px\),\s*0\.34fr\)\s+minmax\(0,\s*1fr\);[\s\S]*gap:\s*var\(--layout-gap\);/m);
  assert.match(styles, /@media \(max-width:\s*1180px\)\s*\{[\s\S]*\.experiment-launchpad,\s*\.fast-grid,\s*\.evolution-grid,\s*\.rounds-grid,\s*\.monitor-grid,\s*\.analyze-grid,\s*\.studio-grid[\s\S]*grid-template-columns:\s*1fr;/m);
});

test("frontend uses RAPID platform branding", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(html, /RAPID/);
  assert.match(source, /RAPID Protein Design Platform/);
  assert.match(source, /Reproducible AI Pipeline for Integrated Design/);
  assert.doesNotMatch(html, /class="brand-title">KBF</);
  assert.doesNotMatch(html, /Protein Pipeline Console/);
});

test("frontend exposes Tailwind-inspired platform color tokens", () => {
  const tailwindEntry = readFileSync(
    new URL("../tailwind-entry.css", import.meta.url),
    "utf8"
  );
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  assert.match(tailwindEntry, /--color-platform-teal/);
  assert.match(tailwindEntry, /--color-platform-emerald/);
  assert.match(tailwindEntry, /--color-platform-slate/);
  assert.match(styles, /--surface-canvas/);
  assert.match(styles, /--action-primary/);
  assert.match(styles, /--state-success/);
});

test("topbar removes duplicated usage and settings controls", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.doesNotMatch(html, /id="helpBtn"/);
  assert.doesNotMatch(html, /id="settingsBtn"/);
  assert.doesNotMatch(source, /Usage for the static guide/);
  assert.doesNotMatch(source, /Settings for report language/);
  assert.doesNotMatch(source, /리포트 언어는 Settings/);
});

test("home screen is an experiment launchpad", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  assert.match(html, /class="experiment-launchpad"/);
  assert.match(source, /home\.launchpad\.newExperiment/);
  assert.match(source, /home\.launchpad\.loadRun/);
  assert.match(source, /home\.launchpad\.analyzeResults/);
  assert.match(styles, /\.experiment-launchpad/);
});

test("home new experiment opens a mode chooser instead of forcing fast launch", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  assert.match(html, /data-home-target="experiment"/);
  assert.match(html, /id="experimentChoicePanel"/);
  assert.match(html, /data-experiment-target="fast"/);
  assert.match(html, /data-experiment-target="advanced"/);
  assert.match(html, /data-experiment-target="surrogate"/);
  assert.match(html, /data-experiment-target="evolution"/);
  assert.match(html, /data-experiment-target="studio"/);
  assert.doesNotMatch(html, /class="home-mode-card launchpad-primary" type="button" data-home-target="fast"/);

  assert.match(source, /function openExperimentChoicePanel/);
  assert.match(source, /function handleExperimentChoice/);
  assert.match(source, /querySelectorAll\("\[data-experiment-target\]"\)/);
  assert.match(source, /"home\.experimentChoice\.surrogate\.title"/);
  assert.match(source, /"home\.experimentChoice\.advanced\.title"/);
  assert.match(source, /"home\.experimentChoice\.studio\.desc"/);
  assert.match(styles, /\.experiment-choice-grid/);
  assert.match(styles, /\.overlay-card\.experiment-choice-card\s*\{[\s\S]*width:\s*min\(960px,\s*calc\(100vw - 32px\)\);/m);
  assert.match(styles, /\.experiment-choice-grid\s*\{[\s\S]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);/m);
  assert.match(styles, /@media \(min-width:\s*1040px\)\s*\{[\s\S]*\.experiment-choice-grid\s*\{[\s\S]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\);/m);
  assert.match(styles, /\.experiment-choice-btn strong\s*\{[\s\S]*word-break:\s*keep-all;/m);
  assert.match(styles, /\.experiment-choice-card \.ghost\s*\{[\s\S]*white-space:\s*nowrap;/m);
});

test("surrogate triage is exposed through Advanced instead of a standalone sidebar tab", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.doesNotMatch(html, /id="tabBtnSurrogate"/);
  assert.doesNotMatch(html, /id="tab-surrogate"/);
  assert.match(html, /data-experiment-target="surrogate"/);
  assert.match(
    source,
    /const TAB_OPTIONS = \["home", "fast", "advanced", "evolution", "studio", "monitor", "cath", "rounds", "analyze", "mcp"\];/
  );
  assert.match(source, /applySurrogatePresetToAdvanced\(\);/);
  assert.match(source, /appendSurrogateTriageBoard\(\);/);
  assert.match(source, /surrogate_triage_enabled:\s*true/);
  assert.match(source, /surrogate_triage_scope:\s*"pooled_tiers"/);
  assert.match(source, /num_seq_per_tier:\s*3333/);
  assert.doesNotMatch(source, /rfd3_use:\s*false/);
  assert.doesNotMatch(source, /bioemu_use:\s*false/);
  assert.match(source, /If you choose Random Forest, Ridge, XGBoost, LightGBM, or Rank ensemble/);
  assert.match(source, /Random Forest, Ridge, XGBoost, LightGBM, Rank ensemble 중 하나를 직접 고르면/);
});

test("analyze tab renders surrogate model selection and comparison summaries", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(html, /id="surrogateTriageSummary"/);
  assert.match(source, /function renderSurrogateTriageSummary/);
  assert.match(source, /surrogate_triage/);
  assert.match(source, /surrogate_selected_model/);
  assert.match(source, /"analyze\.surrogate\.selectedModel"/);
  assert.match(source, /"analyze\.chart\.option\.plddtSoluprot"/);
  assert.match(source, /if \(source === "surrogate"\) return "Surrogate";/);
  assert.match(source, /if \(source === "evolution"\) return "Evolution";/);
  assert.match(source, /raw === "evolution"/);
  assert.match(source, /evolution:\s*"#7c3aed"/);
});

test("evolution UI records experimental feedback inside the evolution workflow", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(html, /id="evolutionFeedbackGuide"/);
  assert.match(html, /id="evolutionExperimentSourceRunIdOptions"/);
  assert.match(html, /id="evolutionExperimentSourceRefreshBtn"/);
  assert.match(html, /id="evolutionExperimentMetricValue"/);
  assert.match(html, /id="evolutionSubmitExperiment"/);
  assert.match(html, /id="evolutionExperimentList"/);
  assert.match(source, /function refreshEvolutionExperimentSourceRunChoices/);
  assert.match(source, /function renderEvolutionExperimentSourceRunChoices/);
  assert.match(source, /function submitEvolutionExperiment/);
  assert.match(source, /function refreshEvolutionExperiments/);
  assert.match(source, /function updateEvolutionLabelSourceUi/);
  assert.match(source, /pipeline\.submit_experiment/);
  assert.match(source, /Experiment-guided mode first chooses candidates to measure/);
  assert.match(source, /AF2-score test mode does not use wet-lab measurements/);
  assert.match(source, /실험값 기반 추천은 먼저 측정할 후보를 고릅니다/);
  assert.match(source, /후보 선정 방식/);
  assert.match(source, /candidate_id/);
  assert.match(source, /metric_value/);
});

test("advanced setup uses experiment builder steps", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(source, /setup\.wizard\.workflow/);
  assert.match(source, /setup\.wizard\.criteria/);
  assert.doesNotMatch(source, /setup\.wizard\.scope/);
  assert.doesNotMatch(source, /setup\.wizard\.options/);
});

test("advanced paper mask UI is class-driven", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  assert.match(html, /class="paper-mask-panel/);
  assert.match(source, /paper-mask-suggestion/);
  assert.match(styles, /\.paper-mask-panel/);
  assert.doesNotMatch(html, /style="/);
  assert.doesNotMatch(source, /item\.style\.cssText/);
});

test("fast advanced action opens advanced without requiring target input", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(source, /function openAdvancedFromFast\(\)/);
  assert.match(
    source,
    /fastOpenAdvancedBtn\?\.[\s\S]*?addEventListener\("click", \(\) => \{\s*openAdvancedFromFast\(\);\s*\}\);/
  );
});

test("advanced launch form uses a single ordered settings surface", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  assert.match(html, /class="advanced-launch-frame"/);
  assert.match(html, /id="setupPrimaryLayout"/);
  assert.match(html, /class="setup-lane setup-lane-input"/);
  assert.match(html, /id="questionInputStack"[\s\S]*class="composer"[\s\S]*id="advancedPaperMaskPanel"/m);
  assert.match(html, /class="fast-field setup-custom-run-field hidden"[\s\S]*id="customRunIdInput"/m);
  assert.match(html, /id="setupStepSummary"/);
  assert.match(source, /function renderSetupStepSummary/);
  assert.match(source, /function syncSetupCustomRunField/);
  assert.match(source, /customRunIdInput\?\.closest\("\.setup-custom-run-field"\)/);
  assert.match(source, /syncSetupCustomRunField\(filteredVisibleQuestions\);/);
  assert.match(source, /setupPrimaryLayout\.dataset\.activeStep/);
  assert.match(styles, /\.advanced-launch-frame/);
  assert.match(styles, /\.setup-step-summary/);
  assert.match(styles, /\.paper-mask-panel-featured/);
  assert.match(styles, /\.setup-primary-layout\[data-active-step="input"\]/);
  assert.match(styles, /\.setup-lane-execution\s*\{[\s\S]*?order:\s*-1;/m);
  assert.match(styles, /\.setup-primary-layout:not\(\[data-active-step="input"\]\)\s+\.setup-lane-input/);
  assert.match(source, /"setup\.wizard\.stepMeta": "Current step: \{label\}"/);
  assert.match(source, /"setup\.wizard\.stepMeta": "현재 단계: \{label\}"/);
  assert.match(source, /"setup\.section\.inputDesc": "Start with the target\. Checks, notes, and literature constraints are below\."/);
  assert.match(source, /"setup\.section\.inputDesc": "먼저 타깃을 넣으세요\. 설정 점검, 메모, 문헌 제약 조건은 아래에 있습니다\."/);
});

test("advanced target input is prioritized and rfd3 override input is collapsed", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  assert.match(source, /const safeFileId = String\(q\.id \|\| "file"\)\.replace\(/);
  assert.match(source, /const isRfd3InputOverride = q\.id === "rfd3_input_pdb" && state\.runMode !== "rfd3";/);
  assert.match(source, /const showSetupRfd3InputItem =\s*state\.runMode === "rfd3" \|\|[\s\S]*setupRfd3InputOverrideVisible\(\) \|\|[\s\S]*state\.answers\.rfd3_input_pdb/m);
  assert.match(source, /document\.createElement\(isRfd3InputOverride \? "details" : "div"\)/);
  assert.match(source, /optional-attachment-item/);
  assert.match(source, /const defaultOpen = setupRfd3InputOverrideVisible\(\) \|\| Boolean\(String\(state\.answers\[q\.id\] \|\| ""\)\.trim\(\)\);/);
  assert.match(source, /setupDetailsStateKey\(`attachment-\$\{safeFileId\}`,\s*\{\s*stepId: setupWizardStepId \|\| "input"\s*\}\)/);
  assert.match(source, /attachment-\$\{safeFileId\}/);
  assert.match(styles, /\.attachment-item\.attachment-target_input/);
  assert.match(styles, /\.optional-attachment-item/);
});

test("advanced setup does not expose evolution controls inside normal pipeline runs", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.doesNotMatch(source, /if \(setupWizardStepId === "workflow"\) \{\s*appendEvolutionBoard\(\);\s*\}/m);
  assert.match(html, /id="tab-evolution"/);
  assert.match(source, /"setup\.customRunId\.label"/);
  assert.doesNotMatch(html, /Custom Run ID/);
});

test("advanced optional setup boards are collapsed by default", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  assert.match(source, /function makeOptionalSetupDetails/);
  assert.match(source, /document\.createElement\("details"\)/);
  assert.match(source, /optional-setup-card/);
  assert.match(source, /appendConfigCard\(makeOptionalSetupDetails\(card,\s*\{\s*key: "options"\s*\}\)\);/);
  assert.match(source, /appendConfigCard\(makeOptionalSetupDetails\(card,\s*\{\s*key: "parameters",\s*defaultOpen: false\s*\}\)\);/m);
  assert.match(styles, /\.optional-setup-card/);
  assert.match(styles, /\.optional-setup-summary/);
  assert.match(styles, /\.optional-setup-body/);
});

test("advanced optional setup boards preserve open state across rerenders", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(source, /setupDetailsOpenByKey:\s*\{\}/);
  assert.match(source, /function setupDetailsStateKey\(/);
  assert.match(source, /function rememberSetupDetailsOpen\(/);
  assert.match(source, /details\.addEventListener\("toggle",\s*\(\)\s*=>\s*\{/);
  assert.match(source, /readSetupDetailsOpen\(stateKey,\s*defaultOpen\)/);
  assert.match(source, /makeOptionalSetupDetails\(card,\s*\{\s*key:\s*"options"/);
  assert.match(source, /makeOptionalSetupDetails\(card,\s*\{\s*key:\s*"parameters"/);
  assert.match(source, /makeOptionalSetupDetails\(card,\s*\{\s*key:\s*"constraints"/);
  assert.match(source, /makeOptionalSetupDetails\(card,\s*\{\s*key:\s*"rfd3-detail"/);
  assert.match(source, /makeOptionalSetupDetails\(card,\s*\{\s*key:\s*"evolution"/);
});

test("advanced setup exposes surrogate triage as a switchable AF2 budget layer", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.doesNotMatch(source, /RUN_MODE_OPTIONS[\s\S]*\{ labelKey: "runmode\.surrogate", value: "surrogate" \}/);
  assert.match(source, /const RUN_MODE_PRIMARY_OPTIONS = \[/);
  assert.match(source, /\{ labelKey: "runmode\.standalone", value: "standalone" \}/);
  assert.match(source, /const RUN_MODE_STANDALONE_OPTIONS = RUN_MODE_OPTIONS\.filter/);
  assert.match(source, /"tutorial\.step\.advancedSurrogate\.title"/);
  assert.match(source, /appendWorkflowToggleSetting\("surrogate_triage_enabled", false\)/);
  assert.match(source, /applySurrogateBudgetDefaultsToAnswers\(state\.answers\)/);
  assert.match(source, /"question\.surrogateTriageEnabled\.label"/);
  assert.match(source, /surrogate_triage_enabled/);
  assert.match(source, /surrogate_triage_initial_samples/);
  assert.match(source, /surrogate_triage_top_k/);
  assert.match(source, /surrogate_triage_model/);
  assert.doesNotMatch(source, /state\.answers\.rfd3_use = false;\s*state\.answers\.bioemu_use = false;/);
});

test("evolution setup defaults to experimental feedback label source", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(html, /id="evolutionLabelSourceInput"/);
  assert.match(source, /evolution_label_source:\s*el\.evolutionLabelSourceInput/);
  assert.match(source, /"question\.evolutionLabelSource\.label"/);
  assert.match(source, /"experimental"/);
  assert.match(source, /"in_silico_af2"/);
  assert.match(source, /evolution_objective_metric/);
  assert.match(source, /evolution_experiment_source_run_id/);
});

test("user-facing Korean tutorial copy avoids internal pipeline jargon", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(source, /"question\.runMode\.detail": "모드에 따라 필요한 입력, 실행 시간, 결과를 얼마나 자세히 만들지가 달라집니다\."/);
  assert.match(source, /"tutorial\.step\.homeRound\.body":\s*"먼저 프로젝트를 고른 뒤 이번 실험 회차를 만듭니다\. 이후 실행하는 작업은 그 프로젝트와 회차 아래에 기록됩니다\."/);
  assert.match(source, /"home\.context\.round": "현재 회차"/);
  assert.doesNotMatch(source, /출력 깊이/);
  assert.doesNotMatch(source, /활성 라운드/);
  assert.doesNotMatch(source, /project\/round 정보/);
});

test("platform palette avoids beige-dominant application backgrounds", () => {
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  assert.doesNotMatch(styles, /#f8f4ec/);
  assert.doesNotMatch(styles, /255,\s*253,\s*248/);
  assert.doesNotMatch(styles, /255,\s*249,\s*235/);
  assert.doesNotMatch(styles, /248,\s*245,\s*239/);
  assert.match(styles, /--surface-canvas:\s*oklch\(98\.5% 0\.002 247\.839\)/);
});

test("frontend includes a localized first-run tutorial overlay", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

  assert.match(html, /id="tutorialBtn"/);
  assert.match(html, /id="tutorialOverlay"/);
  assert.match(html, /id="tutorialContents"/);
  assert.match(html, /id="tutorialSpotlight"/);
  assert.match(html, /id="tutorialStepTitle"/);
  assert.match(html, /id="tutorialSkip"/);
  assert.match(html, /id="tutorialNext"/);

  assert.match(source, /const TUTORIAL_STORAGE_KEY = "kbf\.tutorial\.completed\.v3"/);
  assert.match(source, /const TUTORIAL_STEPS = \[/);
  assert.match(source, /const TUTORIAL_SECTIONS = \[/);
  assert.match(source, /function startTutorialSection/);
  assert.match(source, /function renderTutorialContents/);
  assert.match(source, /function maybeShowTutorialOnFirstVisit/);
  assert.match(source, /function openTutorial/);
  assert.match(source, /"action\.tutorial"/);
  assert.match(source, /"tutorial\.contents\.title"/);
  assert.match(source, /"tutorial\.step\.topbar\.title"/);
  assert.match(source, /"tutorial\.step\.evolution\.title"/);
  assert.match(source, /"tutorial\.section\.surrogate\.title"/);
  assert.match(source, /"tutorial\.step\.advancedSurrogate\.title"/);
  assert.match(source, /"tutorial\.step\.surrogateSettings\.title"/);
  assert.match(source, /id:\s*"surrogate"[\s\S]*stepIds:\s*\["advancedSurrogate",\s*"surrogateSettings"\]/m);
  assert.match(source, /"tutorial\.step\.studio\.title"/);
  assert.match(source, /"tutorial\.step\.rounds\.title"/);
  assert.match(source, /"tutorial\.step\.analyze\.title"/);

  assert.match(styles, /\.tutorial-overlay/);
  assert.match(styles, /\.tutorial-spotlight/);
  assert.match(styles, /\.tutorial-contents/);
  assert.match(styles, /\.tutorial-content-btn/);
  assert.match(styles, /\.tutorial-menu-mode/);
  assert.match(styles, /\.tutorial-card/);
});

test("tutorial covers expert workflow controls and downstream review tools", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(source, /id: "homeProject"/);
  assert.match(source, /id: "experimentChoice"/);
  assert.match(source, /id: "home"[\s\S]*?target: "\.home-shell"/m);
  assert.match(source, /id: "home"[\s\S]*?id: "homeProject"[\s\S]*?id: "experimentChoice"/m);
  assert.match(source, /id: "experimentChoice"[\s\S]*?target: "\.experiment-choice-grid"/m);
  assert.match(source, /"tutorial\.step\.experimentChoice\.title"/);
  assert.match(source, /"tutorial\.step\.experimentChoice\.title": "Choose the right start path"/);
  assert.match(source, /"tutorial\.step\.experimentChoice\.title": "시작 경로를 고릅니다"/);
  assert.doesNotMatch(source, /id: "homeRound"/);
  assert.doesNotMatch(source, /target: "#homeCreateRoundBtn"/);
  assert.match(source, /id: "advancedInput"[\s\S]*?setupStep: "input"/m);
  assert.match(source, /id: "advancedInput"[\s\S]*?id: "pdfAgent"[\s\S]*?id: "advancedWorkflow"/m);
  assert.match(source, /id: "advancedWorkflow"[\s\S]*?setupStep: "workflow"/m);
  assert.match(source, /id: "advancedCriteria"[\s\S]*?setupStep: "criteria"/m);
  assert.match(source, /id: "advancedExpert"[\s\S]*?setupStep: "expert"/m);
  assert.match(source, /id: "advancedReview"[\s\S]*?setupStep: "review"/m);
  assert.match(source, /id: "pdfAgent"[\s\S]*?setupStep: "input"[\s\S]*?target: "#advancedPaperMaskPanel"/m);
  assert.match(source, /id: "evolutionSettings"/);
  assert.match(source, /id: "studioCheckpoint"/);
  assert.match(source, /id: "monitorAgent"/);
  assert.match(source, /id: "analyzeHitList"/);
  assert.match(source, /id: "report"/);
  assert.match(source, /id: "copilot"/);
  assert.match(source, /id: "topbarMenu"[\s\S]*?target: "\.topbar-actions"/m);

  assert.match(source, /function applyTutorialStepContext/);
  assert.match(source, /if \(step\?\.id === "experimentChoice"\) \{[\s\S]*?openExperimentChoicePanel\(\{ focusFirst: false \}\);[\s\S]*?return;\s*\}/m);
  assert.match(source, /closeExperimentChoicePanel\(\);[\s\S]*?const setupStep = String\(step\?\.setupStep \|\| ""\)\.trim\(\);/m);
  assert.match(source, /state\.setupStepIndex = stepIndex;/);
  assert.match(source, /"tutorial\.step\.homeProject\.title"/);
  assert.match(source, /"tutorial\.step\.homeRound\.title"/);
  assert.match(source, /"tutorial\.step\.advancedInput\.title"/);
  assert.match(source, /"tutorial\.step\.advancedWorkflow\.title"/);
  assert.match(source, /"tutorial\.step\.advancedCriteria\.title"/);
  assert.match(source, /"tutorial\.step\.advancedExpert\.title"/);
  assert.match(source, /"tutorial\.step\.advancedReview\.title"/);
  assert.match(source, /"tutorial\.step\.pdfAgent\.title"/);
  assert.match(source, /"tutorial\.step\.evolutionSettings\.title"/);
  assert.match(source, /"tutorial\.step\.studioCheckpoint\.title"/);
  assert.match(source, /"tutorial\.step\.monitorAgent\.title"/);
  assert.match(source, /"tutorial\.step\.analyzeHitList\.title"/);
  assert.match(source, /"tutorial\.step\.report\.title"/);
  assert.match(source, /"tutorial\.step\.copilot\.title"/);
  assert.match(source, /"tutorial\.step\.topbar\.title"/);
});

test("tutorial covers the updated model provider menu and custom model add flow", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.match(source, /id: "modelProviders"[\s\S]*?target: "\.model-providers-card"/m);
  assert.match(source, /id: "modelProviderAdd"[\s\S]*?target: "#modelProviderAddPanel"/m);
  assert.match(source, /"tutorial\.step\.modelProviders\.title"/);
  assert.match(source, /"tutorial\.step\.modelProviderAdd\.title"/);
  assert.match(source, /"tutorial\.step\.topbar\.body"[\s\S]*?Model Providers/);
  assert.match(source, /"tutorial\.step\.topbar\.body"[\s\S]*?모델 연결/);
  assert.match(source, /if \(step\?\.id === "modelProviders"\) \{/);
  assert.match(source, /if \(step\?\.id === "modelProviderAdd"\) \{/);
  assert.match(source, /openModelProvidersPanel\(\);/);
  assert.match(source, /setModelProviderAddOpen\(true\);/);
});

test("copilot awaits async replies before adding them to chat history", () => {
  const source = readFileSync(new URL("../app.js", import.meta.url), "utf8");

  assert.doesNotMatch(source, /addCopilotHistory\("ai",\s*generateCopilotReply\(prompt,\s*intentHint\)\);/);
  assert.match(source, /async function submitCopilotPrompt\(rawPrompt,\s*intentHint = ""\)/);
  assert.match(source, /const reply = await generateCopilotReply\(prompt,\s*intentHint\);/);
  assert.match(source, /addCopilotHistory\("ai",\s*reply\);/);
});

test("copilot usage reply follows the active Home tab", async () => {
  const text = await buildCopilotReply({
    prompt: "이 화면 사용법",
    intentHint: "usage",
    lang: "ko",
    snapshot: { tab: "home", runId: "", rows: [], compare: { ready: false } },
  });

  assert.match(text, /프로젝트/);
  assert.match(text, /회차/);
  assert.match(text, /새 실험/);
  assert.doesNotMatch(text, /Analyze에서는/);
  assert.doesNotMatch(text, /Hit List/);
});
