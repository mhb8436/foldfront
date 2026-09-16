# Gemini Reasoning Agent & Data Collection Guide

이 문서는 Protein Pipeline에 통합된 Gemini 기반 추론 엔진의 작동 원리와, 자체 모델 구축을 위한 데이터 수집 체계를 상세히 설명합니다.

---

## 1. 개요 (Overview)

Protein Pipeline은 단순한 실험 자동화를 넘어, **생물학적 데이터에 기반한 의사결정 지원**을 목표로 합니다. 이를 위해 Gemini(2.5 Flash / 3.1 Pro)를 추론 엔진으로 도입하여, 복잡한 파이프라인 수치 데이터를 인간 전문가 수준의 조언으로 변환합니다.

## 2. 시스템 아키텍처 (Architecture)

### 2.1 Gemini 통합 구조
- **Client:** `pipeline-mcp/src/pipeline_mcp/clients/gemini.py`에 구현된 `GeminiClient`가 API 통신을 담당합니다.
- **Safety Settings:** 기술적인 생물학적 분석 중 필터링 오작동을 방지하기 위해 `BLOCK_NONE` 설정을 사용합니다.
- **Dependency Injection:** `PipelineRunner` 초기화 시 Gemini 클라이언트가 주입되며, 전역적으로 접근 가능합니다.

### 2.2 지식 합성 (Context Synthesis)
`_agent_chat_tool`은 Gemini에게 질문을 전달하기 전, 다음 정보를 결합하여 **"현상 인식(Situation Awareness)"**을 구축합니다.

1.  **Run Status:** 현재 실행 중인 단계(Stage)와 상태(State: running/failed/completed).
2.  **Pipeline Summary:** 계층적 BO의 결과 (예: Stage 1 SoluProt 통과율, 현재까지의 최상위 후보 수치).
3.  **Expert Snippets:** 전문가 시스템(`agent_panel`)이 이전에 기록한 기술적 해석들.

---

## 3. 추론 로직 (Reasoning Process)

Gemini는 단순히 질문에 답하는 것이 아니라, 제공된 컨텍스트를 기반으로 다음과 같은 추론 과정을 거칩니다.

### 프롬프트 엔지니어링 전략
시스템 인스트럭션을 통해 Gemini에게 **"단백질 설계 전문가"**의 페르소나를 부여합니다.
- **Bilingual Support:** 사용자의 질문 언어(한국어/영어)를 감지하여 동일한 언어로 답변합니다.
- **Technical Rigor:** "수용성이 낮다"는 막연한 말 대신, "SoluProt 통과율이 20% 미만이므로 sampling_temp를 낮추거나 cutoff를 조정하라"는 구체적인 액션을 제시합니다.

---

## 4. 데이터 수집 체계 (Data Collection Strategy)

미래에 Google Gemini에 의존하지 않는 **독자적인 단백질 설계 특화 LLM**을 학습시키기 위해, 모든 상호작용은 자동으로 기록됩니다.

### 4.1 저장 위치 및 방식
- **경로:** `outputs/_reasoning_data/`
- **파일명:** `dataset_YYYY_MM.jsonl` (월 단위 자동 분할 및 로테이션)
- **포맷:** JSON Lines (데이터 분석 및 학습에 최적화)

### 4.2 데이터 스키마 (Data Schema)
매 Interaction마다 다음 정보가 하나의 JSON 객체로 저장됩니다.

```json
{
  "timestamp": "2026-04-15T18:00:00Z",
  "run_id": "exp_abc_123",
  "model": "gemini-3.1-pro-preview",
  "context": "현재 파이프라인의 수치적 요약 데이터 (Raw Text)",
  "expert_panel_data": ["MSA 깊이 부족", "수용성 필터링 통과율 저하"],
  "prompt": "왜 결과가 안 나오지?",
  "response": "Gemini가 생성한 전문적인 답변 내용",
  "language": "ko"
}
```

---

## 5. 미래 활용 계획 (Future Roadmap)

수집된 데이터는 다음과 같은 단계로 활용됩니다.

1.  **데이터 정제 (Labeling):** 사용자가 'Good' 피드백을 준 데이터를 Gold Standard로 분류.
2.  **지식 증류 (Distillation):** Gemini 3.1 Pro가 생성한 고품질 추론 과정을 Llama 3나 Mistral 같은 오픈소스 모델에 학습.
3.  **RLHF (Human Feedback):** 실제 실험 결과(성공/실패)와 조언의 연관성을 분석하여 모델을 고도화.
4.  **Local Deployment:** 외부 API 없이 서버 내부에서 완벽하게 작동하는 온프레미스 추론 모델 구축.

---

## 6. 설정 방법 (Configuration)

`.env` 파일에서 다음 변수를 통해 제어할 수 있습니다.

- `GEMINI_API_KEY`: Google AI Studio에서 발급받은 키.
- `GEMINI_MODEL`: `gemini-2.5-flash`(속도 우선) 또는 `gemini-3.1-pro-preview`(품질 우선).

---
*Last Updated: 2026-04-15*
