/**
 * Korean names for the stage and model identifiers.
 *
 * The identifier itself is never replaced on screen. Logs, artifact paths and
 * API responses all use it, so a screen that showed only the Korean could not
 * be reconciled with the record. Both are shown.
 *
 * The wording matches docs/용어집.md; that file is the reference if they drift.
 */

export interface Term {
  /** Korean name, shown beside the identifier. */
  label: string
  /** One line of explanation, shown as a tooltip. */
  hint: string
}

export const STAGE_TERMS: Record<string, Term> = {
  msa: { label: '다중서열정렬', hint: '닮은 서열을 모아 정렬한다. 뒤 단계의 판단 근거가 된다' },
  mmseqs: { label: '다중서열정렬', hint: '닮은 서열을 모아 정렬한다. 뒤 단계의 판단 근거가 된다' },
  rfd3: { label: '백본 생성', hint: '서열 없이 3차원 뼈대 모양만 여러 개 만든다' },
  bioemu: { label: '구조 앙상블', hint: '한 구조가 취할 수 있는 여러 자세를 표본으로 뽑는다' },
  design: { label: '서열 설계', hint: '뼈대를 주고 그 모양이 되는 아미노산 서열을 찾는다' },
  proteinmpnn: { label: '서열 설계', hint: '뼈대를 주고 그 모양이 되는 아미노산 서열을 찾는다' },
  soluprot: { label: '가용성 예측', hint: '대장균에서 녹는지를 서열만 보고 점수로 낸다' },
  af2: { label: '구조 예측', hint: '설계한 서열이 의도한 모양으로 접히는지 확인한다' },
  colabfold: { label: '구조 예측', hint: '설계한 서열이 의도한 모양으로 접히는지 확인한다' },
  novelty: { label: '신규성 평가', hint: '설계 결과가 야생형과 얼마나 다른지 센다' },
  diffdock: { label: '도킹', hint: '단백질과 화합물이 어떻게 맞물리는지 예측한다' },
  esm: { label: '서열 임베딩', hint: '서열을 수치 벡터로 바꾼다. 유사도 비교에 쓴다' },
}

/** What a metric means, so a column of numbers is not read blind. */
export const METRIC_TERMS: Record<string, Term> = {
  depth: { label: '정렬 깊이', hint: '모은 유사 서열 수. 많을수록 근거가 두텁다' },
  coverage: { label: '정렬 범위', hint: '대상 서열 중 정렬된 비율' },
  backbones: { label: '뼈대 수', hint: '만든 백본 후보 수' },
  mean_rmsd: { label: '평균 편차', hint: '기준 구조와의 평균 거리(Å). 낮을수록 가깝다' },
  sequences: { label: '설계 서열 수', hint: '설계한 후보 서열 수' },
  pass_rate: { label: '가용성 통과율', hint: '가용성 기준을 넘긴 후보 비율' },
  passed: { label: '통과 수', hint: '다음 단계로 넘어간 후보 수' },
  plddt: { label: '예측 신뢰도', hint: '0~100. 구조가 좋다가 아니라 예측이 확실하다는 뜻이다' },
  rmsd: { label: '구조 편차', hint: '의도한 구조와의 거리(Å). 낮을수록 의도대로 접혔다' },
  novel: { label: '신규 잔기 수', hint: '야생형과 다른 자리 수' },
}

export function stageTerm(id: string | null | undefined): Term | undefined {
  return id ? STAGE_TERMS[id] : undefined
}

/** Accepts a stage-qualified name such as `soluprot.pass_rate`. */
export function metricTerm(key: string): Term | undefined {
  const bare = key.includes('.') ? key.slice(key.lastIndexOf('.') + 1) : key
  return METRIC_TERMS[bare]
}
