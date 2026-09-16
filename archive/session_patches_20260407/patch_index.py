import re

with open("frontend/index.html", "r") as f:
    content = f.read()

# Find the evolution-options-card
pattern = r'(<aside class="evolution-options-card mode-shell-placeholder">.*?<label class="fast-field">\s*<span data-i18n="evolution.samplesPerRound.label">Samples Per Round</span>\s*<input id="evolutionSamplesPerRoundInput" type="number" min="1" step="1" value="5" />\s*</label>)'

replacement = r'''\1
                  <div class="panel-header" style="margin-top: 1rem;">
                    <h4 data-i18n="evolution.filtering.title">Filtering</h4>
                  </div>
                  <label class="fast-field">
                    <span data-i18n="evolution.af2PlddtCutoff.label">ColabFold pLDDT Cutoff</span>
                    <input id="evolutionAf2PlddtCutoffInput" type="number" min="0" max="100" step="0.1" value="85" />
                  </label>
                  <label class="fast-field">
                    <span data-i18n="evolution.af2RmsdCutoff.label">ColabFold RMSD Cutoff</span>
                    <input id="evolutionAf2RmsdCutoffInput" type="number" min="0.01" step="0.01" value="2.0" />
                  </label>
                  <label class="fast-field">
                    <span data-i18n="evolution.relaxScoreCutoff.label">Rosetta Relax Score/Residue Cutoff</span>
                    <input id="evolutionRelaxScoreCutoffInput" type="number" step="0.1" value="0.0" />
                  </label>
                  <label class="fast-field">
                    <span data-i18n="evolution.bioemuTargetRmsdCutoff.label">BioEmu Target RMSD Cutoff</span>
                    <input id="evolutionBioemuTargetRmsdCutoffInput" type="number" min="0.01" step="0.01" value="2.0" />
                  </label>
                  <label class="fast-field">
                    <span data-i18n="evolution.rfd3TargetRmsdCutoff.label">RFD3 Target RMSD Cutoff</span>
                    <input id="evolutionRfd3TargetRmsdCutoffInput" type="number" min="0.01" step="0.01" value="2.0" />
                  </label>

                  <div class="panel-header" style="margin-top: 1rem;">
                    <h4 data-i18n="evolution.constraints.title">Constraints</h4>
                  </div>
                  <label class="fast-field">
                    <span data-i18n="evolution.bioemuSteeringConfig.label">BioEmu Steering Config</span>
                    <textarea id="evolutionBioemuSteeringConfigInput" rows="3" placeholder="JSON config"></textarea>
                  </label>
                  <label class="fast-field">
                    <span data-i18n="evolution.fixedPositionsExtra.label">Fixed Positions Extra</span>
                    <textarea id="evolutionFixedPositionsExtraInput" rows="3" placeholder="A:6,10;*:120"></textarea>
                  </label>'''

new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

if new_content != content:
    with open("frontend/index.html", "w") as f:
        f.write(new_content)
    print("Patched index.html successfully.")
else:
    print("Failed to patch index.html.")
