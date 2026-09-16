import re

with open("frontend/app.js", "r") as f:
    content = f.read()

# 1. Add elements to el
el_pattern = r'(evolutionSamplesPerRoundInput:\s*document\.getElementById\("evolutionSamplesPerRoundInput"\),)'
el_replacement = r'''\1
  evolutionAf2PlddtCutoffInput: document.getElementById("evolutionAf2PlddtCutoffInput"),
  evolutionAf2RmsdCutoffInput: document.getElementById("evolutionAf2RmsdCutoffInput"),
  evolutionRelaxScoreCutoffInput: document.getElementById("evolutionRelaxScoreCutoffInput"),
  evolutionBioemuTargetRmsdCutoffInput: document.getElementById("evolutionBioemuTargetRmsdCutoffInput"),
  evolutionBioemuSteeringConfigInput: document.getElementById("evolutionBioemuSteeringConfigInput"),
  evolutionRfd3TargetRmsdCutoffInput: document.getElementById("evolutionRfd3TargetRmsdCutoffInput"),
  evolutionFixedPositionsExtraInput: document.getElementById("evolutionFixedPositionsExtraInput"),'''
content = re.sub(el_pattern, el_replacement, content)

# 2. Add i18n strings (English)
en_pattern = r'("evolution\.samplesPerRound\.label":\s*"Samples Per Round",)'
en_replacement = r'''\1
    "evolution.filtering.title": "Filtering",
    "evolution.constraints.title": "Constraints",
    "evolution.af2PlddtCutoff.label": "ColabFold pLDDT Cutoff",
    "evolution.af2RmsdCutoff.label": "ColabFold RMSD Cutoff",
    "evolution.relaxScoreCutoff.label": "Rosetta Relax Score/Residue Cutoff",
    "evolution.bioemuTargetRmsdCutoff.label": "BioEmu Target RMSD Cutoff",
    "evolution.bioemuSteeringConfig.label": "BioEmu Steering Config",
    "evolution.rfd3TargetRmsdCutoff.label": "RFD3 Target RMSD Cutoff",
    "evolution.fixedPositionsExtra.label": "Fixed Positions Extra",'''
content = re.sub(en_pattern, en_replacement, content)

# 3. Add i18n strings (Korean)
ko_pattern = r'("evolution\.samplesPerRound\.label":\s*"회차당 샘플 수",)'
ko_replacement = r'''\1
    "evolution.filtering.title": "필터링 (Filtering)",
    "evolution.constraints.title": "구조 제약 (Constraints)",
    "evolution.af2PlddtCutoff.label": "ColabFold pLDDT 컷오프",
    "evolution.af2RmsdCutoff.label": "ColabFold RMSD 컷오프",
    "evolution.relaxScoreCutoff.label": "Rosetta Relax score/residue 컷오프",
    "evolution.bioemuTargetRmsdCutoff.label": "BioEmu target RMSD 컷오프",
    "evolution.bioemuSteeringConfig.label": "BioEmu steering config",
    "evolution.rfd3TargetRmsdCutoff.label": "RFD3 target RMSD 컷오프",
    "evolution.fixedPositionsExtra.label": "고정 위치 추가 (Fixed Positions Extra)",'''
content = re.sub(ko_pattern, ko_replacement, content)

# 4. Update initEvolutionLauncher
init_pattern = r'(evolution_samples_per_round:\s*Number\.parseInt\(el\.evolutionSamplesPerRoundInput\?\.value\s*\|\|\s*"5",\s*10\),)'
init_replacement = r'''\1
      af2_plddt_cutoff: Number.parseFloat(el.evolutionAf2PlddtCutoffInput?.value || "85"),
      af2_rmsd_cutoff: Number.parseFloat(el.evolutionAf2RmsdCutoffInput?.value || "2.0"),
      relax_score_per_residue_cutoff: Number.parseFloat(el.evolutionRelaxScoreCutoffInput?.value || "0.0"),
      bioemu_target_rmsd_cutoff: Number.parseFloat(el.evolutionBioemuTargetRmsdCutoffInput?.value || "2.0"),
      bioemu_steering_config_text: el.evolutionBioemuSteeringConfigInput?.value || "",
      rfd3_target_rmsd_cutoff: Number.parseFloat(el.evolutionRfd3TargetRmsdCutoffInput?.value || "2.0"),
      fixed_positions_extra: el.evolutionFixedPositionsExtraInput?.value || "",'''
content = re.sub(init_pattern, init_replacement, content)

with open("frontend/app.js", "w") as f:
    f.write(content)
print("Patched app.js successfully.")
