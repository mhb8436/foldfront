function isKo(lang = "en") {
  return String(lang || "").trim().toLowerCase().startsWith("ko");
}

function pickerColorModeLabel(colorMode = "secondary", lang = "en") {
  const ko = isKo(lang);
  const mode = String(colorMode || "secondary").trim().toLowerCase();
  if (mode === "chain") return ko ? "체인별 색상" : "chain";
  if (mode === "spectrum") return ko ? "N→C spectrum" : "N→C spectrum";
  return ko ? "2차구조 기반" : "secondary structure";
}

function localizedResidueState(stateKey = "", lang = "en") {
  const ko = isKo(lang);
  const key = String(stateKey || "").trim().toLowerCase();
  if (key === "selected") return ko ? "선택됨" : "selected";
  if (key === "surface") return ko ? "surface" : "surface";
  if (key === "core") return ko ? "core" : "core";
  if (key === "interface") return ko ? "interface" : "interface";
  return key;
}

export function buildResiduePickerViewerLegendLines({ colorMode = "secondary", lang = "en" } = {}) {
  if (isKo(lang)) {
    return [
      "Cartoon 보기",
      `기본 색: ${pickerColorModeLabel(colorMode, lang)}`,
      "선택 잔기: 주황",
      "잔기에 마우스를 올리면 정보 표시",
    ];
  }
  return [
    "Cartoon view",
    `Base colors: ${pickerColorModeLabel(colorMode, lang)}`,
    "Selected residue: orange",
    "Hover a residue to inspect it",
  ];
}

export function buildCompareViewerLegendLines({ compareMode = "structure", lang = "en" } = {}) {
  const mode = String(compareMode || "structure").trim().toLowerCase();
  if (mode === "sequence") {
    return isKo(lang)
      ? [
          "회색: backbone 기본",
          "파랑: 기준 서열 차이",
          "주황: 후보 서열 차이",
          "청록: 선택 잔기",
        ]
      : [
          "Gray: aligned backbone",
          "Blue: reference sequence-only diff",
          "Orange: candidate sequence-only diff",
          "Teal: selected residue",
        ];
  }
  return isKo(lang)
    ? [
        "회색: 정렬된 backbone",
        "황색: 1.5-3.0A 이동",
        "적색: >3.0A 이동",
        "청록: 선택 잔기",
      ]
    : [
        "Gray: aligned backbone",
        "Amber: 1.5-3.0A shift",
        "Red: >3.0A shift",
        "Teal: selected residue",
      ];
}

export function buildResiduePickerHoverText({
  chain = "_",
  resi = "",
  resn = "",
  selected = false,
  exposureClass = "",
  interfaceHit = false,
  lang = "en",
} = {}) {
  const parts = [`${String(chain || "_")}:${String(resi || "").trim()} ${String(resn || "").trim()}`.trim()];
  if (selected) parts.push(localizedResidueState("selected", lang));
  if (exposureClass) parts.push(localizedResidueState(exposureClass, lang));
  if (interfaceHit) parts.push(localizedResidueState("interface", lang));
  return parts.filter(Boolean).join(" · ");
}

export function buildCompareHoverText({
  chain = "_",
  resi = "",
  resn = "",
  compareMode = "structure",
  distance = null,
  sequenceSide = "",
  selected = false,
  lang = "en",
} = {}) {
  const parts = [`${String(chain || "_")}:${String(resi || "").trim()} ${String(resn || "").trim()}`.trim()];
  const mode = String(compareMode || "structure").trim().toLowerCase();
  if (mode === "structure" && Number.isFinite(Number(distance))) {
    parts.push(`d=${Number(distance).toFixed(2)}A`);
  }
  const seqSide = String(sequenceSide || "").trim().toLowerCase();
  if (mode === "sequence" && seqSide) {
    if (isKo(lang)) {
      parts.push(seqSide === "left" ? "기준 서열 차이" : "후보 서열 차이");
    } else {
      parts.push(seqSide === "left" ? "reference-only diff" : "candidate-only diff");
    }
  }
  if (selected) parts.push(localizedResidueState("selected", lang));
  return parts.filter(Boolean).join(" · ");
}
