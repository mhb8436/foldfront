import re


def patch_index_html():
    with open("frontend/index.html", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Add tab button
    btn_html = """              <button
                id="tabBtnEvolution"
                class="tab-btn app-nav-btn"
                role="tab"
                aria-selected="false"
                aria-controls="tab-evolution"
                data-tab="evolution"
                data-i18n="tabs.evolution"
              >
                Evolution
              </button>
"""
    if 'id="tabBtnEvolution"' not in content:
        content = content.replace(
            '              <button\n                id="tabBtnAdvanced"',
            btn_html + '              <button\n                id="tabBtnAdvanced"',
        )

    # 2. Add tab panel
    panel_html = """        <section
          id="tab-evolution"
          class="tab-panel"
          data-tab="evolution"
          role="tabpanel"
          aria-labelledby="tabBtnEvolution"
        >
          <div class="tab-content-shell">
            <div class="panel panel-block fast-shell">
              <div class="fast-grid">
                <div class="fast-input-card">
                  <div class="panel-header">
                    <h2 data-i18n="evolution.title">Evolution Studio</h2>
                    <p data-i18n="evolution.desc">
                      Fully automated Bayesian Optimization for protein evolution.
                    </p>
                  </div>
                  <label class="fast-field">
                    <span data-i18n="evolution.input.label">Target PDB</span>
                    <small data-i18n="evolution.input.help">Provide a raw PDB sequence.</small>
                    <textarea
                      id="evolutionTargetInput"
                      rows="10"
                      placeholder="Paste PDB here."
                      data-i18n-placeholder="evolution.input.placeholder"
                    ></textarea>
                  </label>
                  <div class="fast-inline-actions">
                    <input id="evolutionTargetFile" type="file" class="hidden" accept=".pdb,.ent,.txt" />
                    <button id="evolutionLoadFileBtn" class="ghost" type="button" data-i18n="fast.action.loadFile">
                      Load File
                    </button>
                  </div>
                </div>
                <div class="fast-options-card">
                  <div class="panel-header">
                    <h2 data-i18n="evolution.options.title">Evolution Parameters</h2>
                  </div>
                  <div class="fast-options-grid">
                    <label class="fast-field">
                      <span data-i18n="evolution.targetRmsd">Target RMSD</span>
                      <input type="number" id="evolutionTargetRmsd" value="2.0" step="0.1" />
                    </label>
                    <label class="fast-field">
                      <span data-i18n="evolution.boIters">BO Iterations</span>
                      <input type="number" id="evolutionBoIters" value="10" step="1" />
                    </label>
                  </div>
                  <div class="fast-launch-actions">
                    <button id="evolutionRunBtn" class="btn btn-primary btn-large" data-i18n="evolution.action.run">
                      Run Evolution
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

"""
    if 'id="tab-evolution"' not in content:
        content = content.replace(
            '        <section\n          id="tab-advanced"',
            panel_html + '        <section\n          id="tab-advanced"',
        )

    with open("frontend/index.html", "w", encoding="utf-8") as f:
        f.write(content)


def patch_app_js():
    with open("frontend/app.js", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Add to TAB_OPTIONS
    if '"evolution"' not in content:
        content = content.replace(
            'const TAB_OPTIONS = ["home", "fast", "advanced", "studio", "monitor", "rounds", "analyze", "mcp"];',
            'const TAB_OPTIONS = ["home", "evolution", "fast", "advanced", "studio", "monitor", "rounds", "analyze", "mcp"];',
        )

    # 2. Add to I18N
    if '"tabs.evolution"' not in content:
        content = content.replace(
            '"tabs.fast": "Fast",',
            '"tabs.evolution": "Evolution",\n    "tabs.fast": "Fast",',
        )
        content = content.replace(
            '"tabs.fast": "빠른 실행",',
            '"tabs.evolution": "진화 (Evolution)",\n    "tabs.fast": "빠른 실행",',
        )

    # 3. Add elements to `el`
    if "evolutionTargetInput:" not in content:
        el_additions = """  evolutionTargetInput: document.getElementById("evolutionTargetInput"),
  evolutionTargetFile: document.getElementById("evolutionTargetFile"),
  evolutionLoadFileBtn: document.getElementById("evolutionLoadFileBtn"),
  evolutionTargetRmsd: document.getElementById("evolutionTargetRmsd"),
  evolutionBoIters: document.getElementById("evolutionBoIters"),
  evolutionRunBtn: document.getElementById("evolutionRunBtn"),
"""
        content = content.replace(
            '  fastTargetInput: document.getElementById("fastTargetInput"),',
            el_additions
            + '  fastTargetInput: document.getElementById("fastTargetInput"),',
        )

    # 4. Add initialization logic
    init_logic = """
let evolutionLauncherInitialized = false;

async function loadEvolutionTargetFile(file) {
  if (!file || typeof file.text !== "function") return;
  const text = await file.text();
  if (el.evolutionTargetInput) {
    el.evolutionTargetInput.value = String(text || "");
  }
  setMessage(t("fast.message.fileLoaded", { name: file.name || "input" }), "ai");
}

function initEvolutionLauncher() {
  if (evolutionLauncherInitialized) return;
  el.evolutionLoadFileBtn?.addEventListener("click", () => {
    el.evolutionTargetFile?.click();
  });
  el.evolutionTargetFile?.addEventListener("change", async (event) => {
    const file = event?.target?.files?.[0];
    if (!file) return;
    await loadEvolutionTargetFile(file);
    event.target.value = "";
  });
  el.evolutionRunBtn?.addEventListener("click", async () => {
    const targetInput = String(el.evolutionTargetInput?.value || "").trim();
    if (!targetInput) {
      setMessage("Target PDB is required for Evolution.", "ai");
      setActiveTab("evolution");
      return;
    }
    
    // Set up state for evolution
    state.answers.evolution_mode = true;
    state.answers.target_pdb_text = targetInput;
    state.answers.target_rmsd = Number.parseFloat(el.evolutionTargetRmsd?.value || "2.0");
    state.answers.bo_iters = Number.parseInt(el.evolutionBoIters?.value || "10", 10);
    
    // Set default pipeline options for evolution
    state.answers.run_mode = "pipeline";
    state.answers.start_from = "msa";
    state.answers.stop_after = "af2";
    
    await runPipeline();
  });
  evolutionLauncherInitialized = true;
}
"""
    if "initEvolutionLauncher" not in content:
        content = content.replace(
            "function initFastLauncher() {",
            init_logic + "\nfunction initFastLauncher() {",
        )

    # 5. Call initEvolutionLauncher in initTabs
    if "initEvolutionLauncher();" not in content:
        content = content.replace(
            "  initFastLauncher();", "  initEvolutionLauncher();\n  initFastLauncher();"
        )

    with open("frontend/app.js", "w", encoding="utf-8") as f:
        f.write(content)


if __name__ == "__main__":
    patch_index_html()
    patch_app_js()
    print("Patched index.html and app.js")
