# 승계 기준 (Upstream)

본 저장소는 아래 원본을 승계하여 개발한다.

| | |
|---|---|
| 원본 | https://github.com/sblabkribb/protein_pipeline |
| 명칭 | **RAPID** — Reproducible Pipeline for Solubility-Oriented Protein Redesign with Resource-Aware Surrogate Triage |
| 버전 | **v1.0.29** (릴리스 2026-07-13) |
| 기준 커밋 | `df2140a84012c280a8d967535a5343fa020d3964` (2026-08-20 17:17:56 +0900) |
| 태그 | `upstream-v1.0.29` |
| 라이선스 | **MIT** — Copyright (c) 2026 Yaeseong Park, Korea Research Institute of Bioscience and Biotechnology (KRIBB) |
| DOI | 10.5281/zenodo.20619413 |
| 내려받은 날 | 2026-09-16 |

## 라이선스 준수

MIT License 조건에 따라 **원본 저작권 고지와 라이선스 사본을 `LICENSE` 에 그대로 유지**한다.
원본 `README.md` 는 `upstream-v1.0.29` 태그에 그대로 남아 있다 — `git show upstream-v1.0.29:README.md`.
`CITATION.cff` 의 인용 요청에 따라 산출물과 문서에서 원본을 인용한다.

## 원본과의 차이를 확인하는 방법

태그는 원격에 없다. 원본 이력을 이 저장소에 복제하지 않기 때문이다. 처음 한 번 붙인다.

```bash
#  기준 커밋에 태그를 붙입니다. 원본 이력은 이 저장소에 복제하지 않습니다.
git remote add upstream https://github.com/sblabkribb/protein_pipeline.git
git fetch upstream df2140a84012c280a8d967535a5343fa020d3964
git tag upstream-v1.0.29 df2140a84012c280a8d967535a5343fa020d3964
```

```bash
git diff upstream-v1.0.29 --stat        # 변경 요약
git log upstream-v1.0.29..HEAD --oneline # 승계 이후 작업 이력
```

## upstream 변경분 반영

```bash
git fetch upstream
git log upstream-v1.0.29..upstream/main --oneline
```
