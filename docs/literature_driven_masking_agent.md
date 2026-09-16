# Literature-Driven Masking Agent (Paper-to-Mask) Guide

이 문서는 연구 논문(PDF/텍스트)에서 단백질 설계 제약 조건(Masking)을 자동으로 추출하고 파이프라인에 적용하는 **Literature-Driven Masking Agent**의 작동 원리와 사용 방법을 설명합니다.

---

## 1. 개요 (Overview)

단백질 설계 시 활성 부위(Active site)나 결합 계면(Binding interface)을 고정하는 작업은 매우 중요하지만, 논문을 읽고 일일이 잔기 번호를 찾는 과정은 번거롭고 실수하기 쉽습니다. 이 에이전트는 **Gemini 3.1 Pro**의 문서 이해 능력을 활용하여 논문에서 핵심 잔기를 자동으로 찾아내고, 사용자가 3D 구조와 대조하며 즉시 적용할 수 있게 돕습니다.

## 2. 시스템 아키텍처 (Architecture)

### 2.1 논문 분석 도구 (`pipeline.analyze_paper_for_masking`)
- **Text Extraction:** `pypdf` 라이브러리를 사용하여 업로드된 PDF 파일에서 텍스트 레이어를 추출합니다.
- **Contextual Reasoning:** 추출된 논문 텍스트와 현재 설계 중인 단백질 서열을 Gemini 모델에 함께 전달합니다.
- **Structured Output:** AI는 분석 결과를 단순 텍스트가 아닌, 시스템이 즉시 처리 가능한 JSON 스키마로 반환합니다.

### 2.2 프론트엔드 통합
- **Modular UI:** Fast, Evolution, Advanced 등 모든 주요 탭에 독립적인 업로드 및 리뷰 UI가 통합되어 있습니다.
- **State Synchronization:** 사용자가 제안된 잔기를 선택(Apply)하면, `app.js`의 전역 상태인 `fixed_positions_extra`에 실시간으로 병합됩니다.

---

## 3. 추론 및 검증 로직 (Reasoning & Grounding)

단순한 번호 추출을 넘어, 데이터의 신뢰성을 확보하기 위해 다음 로직이 적용됩니다.

### 3.1 근거 기반 추출 (Snippet Grounding)
AI는 잔기를 제안할 때 반드시 논문 내의 **직접적인 인용 문구(`evidence`)**를 함께 제출해야 합니다. 사용자는 UI의 툴팁을 통해 AI가 왜 이 잔기를 고정하라고 했는지 논문의 맥락을 즉시 확인할 수 있습니다.

### 3.2 번호 체계 검증 (Numbering Alignment)
논문에서 언급된 번호와 사용자의 PDB 번호 체계가 다를 경우를 대비하여, AI가 주변 서열 맥락을 분석합니다. 불일치가 의심될 경우 `confidence: "low_sequence_mismatch"` 플래그를 반환하며, UI에 **"⚠️ 서열 번호 불일치 의심"** 경고를 표시합니다.

---

## 4. 사용자 워크플로우 (Human-in-the-Loop)

시스템은 AI의 제안을 자동으로 적용하지 않고, 반드시 인간 전문가의 검토 단계를 거칩니다.

1.  **Upload:** 논문 PDF를 업로드하고 "Analyze" 버튼을 클릭합니다.
2.  **Review:** 나타나는 "AI Suggested Masks" 패널에서 리스트를 검토합니다.
    -   각 항목의 근거 문구를 확인합니다.
    -   적용을 원치 않는 항목은 체크박스를 해제합니다.
3.  **Apply:** "Apply to Constraints"를 클릭하여 실제 설계 파라미터에 반영합니다.
4.  **Visual Check:** 기존 `Residue Picker (3D Viewer)`를 열어 3D 구조상에서 해당 잔기들이 올바르게 선택되었는지 시각적으로 최종 확인합니다.

---

## 5. 데이터 활용 및 로깅

이 에이전트를 통한 모든 활동은 `outputs/_reasoning_data/`에 함께 기록됩니다. 

- 사용자가 AI의 제안 중 어떤 것을 채택하고 어떤 것을 거부했는지에 대한 데이터는 추후 **"논문 기반 단백질 설계 제약 조건 추출 모델"**을 고도화하기 위한 RLHF(Human Feedback) 데이터셋으로 활용됩니다.

---

## 6. 설정 방법 (Configuration)

이 기능은 `Gemini Reasoning Agent`와 동일한 환경 변수 설정을 공유합니다.

- `GEMINI_API_KEY`: Google AI Studio API 키.
- `GEMINI_MODEL`: `gemini-3.1-pro-preview` 권장 (복잡한 논문 맥락 파악에 최적화).

---
*Last Updated: 2026-04-16*
