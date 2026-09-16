# RunPod Admin TODO

## 목표
- `protein_pipeline`가 실제로 사용하는 RunPod Serverless endpoint를 한 화면에서 확인한다.
- endpoint별 GPU 타입, workers min/max, autoscaler, idle timeout, billing을 운영자가 직접 조정한다.
- 별도 빌드 없이 `frontend/` 정적 서빙만으로 접근 가능하게 만든다.

## MLflow를 주 솔루션으로 쓰지 않은 이유
- MLflow는 실험 추적, 모델 레지스트리, 배포 워크플로에 강점이 있다.
- 이번 요구사항은 `RunPod Serverless endpoint 운영 UI`에 가깝다.
- 즉 필요한 것은 experiment tracking UI가 아니라 `endpoint inventory + scaling patch + billing view + pipeline mapping` 이다.
- 그래서 MLflow를 붙이는 것보다, 현재 `pipeline-mcp`에 RunPod 관리 API를 추가하고 정적 운영 콘솔을 새로 만드는 편이 범위와 유지보수 비용 면에서 맞다.

## 이번 턴에서 완료한 범위
- [x] `pipeline.runpod_list_endpoints` 추가
- [x] `pipeline.runpod_get_endpoint` 추가
- [x] `pipeline.runpod_update_endpoint` 추가
- [x] `pipeline.runpod_list_billing` 추가
- [x] RunPod admin 도구를 admin-only로 제한
- [x] `frontend/runpod-admin/` 독립 UI 추가
- [x] managed endpoint 강조 표시
- [x] quick action (`Warm x1`, `Burst x4`, `Pause`) 추가
- [x] endpoint 설정 patch form 추가
- [x] 최근 billing 요약 카드/테이블 추가

## 바로 다음 단계
- [ ] GPU catalog API를 붙여 `gpuTypeIds`를 free-text 대신 선택형으로 바꾸기
- [ ] endpoint create/delete workflow 추가
- [ ] billing 시계열 차트와 endpoint별 드릴다운 추가
- [ ] 변경 이력/audit log 저장
- [ ] 알람 룰 추가
  - workersMax=0 상태 지속
  - managed endpoint missing
  - billing 급증

## 운영 체크리스트
1. `pipeline-mcp`가 최신 코드로 재시작되어야 한다.
2. `frontend/`를 정적으로 서빙하면 `/runpod-admin/` 경로로 접근할 수 있다.
3. 인증이 켜져 있으면 admin 계정으로 로그인해야 한다.
4. API base는 보통 reverse proxy 환경에서는 `/pipeline/api`, 로컬에서는 `http://127.0.0.1:18080` 이다.

## 파일 구성
- `frontend/runpod-admin/index.html`
- `frontend/runpod-admin/styles.css`
- `frontend/runpod-admin/app.js`
- `frontend/runpod-admin/lib.js`
