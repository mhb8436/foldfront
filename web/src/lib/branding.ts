/**
 * 배포처별 표기.
 *
 * foldfront 는 제품이고 기관 표기는 배포 설정이다. 화면에 특정 기관 이름을 박지 않는다.
 * 값은 빌드 시점에 `.env` 로 주입한다(`web/.env.example` 참조).
 */

const env = import.meta.env

export const branding = {
  /** 헤더 좌측 제품명 */
  product: env.VITE_PRODUCT_NAME || 'foldfront',
  /** 제품 한 줄 설명. 헤더 제목으로 쓴다 */
  title: env.VITE_APP_TITLE || '단백질 설계 자동화 플랫폼',
  /** 운영 기관. 비우면 헤더·푸터에서 해당 자리를 생략한다 */
  organization: env.VITE_ORG_NAME || '',
  /** 푸터 저작권 표기. 비우면 「© <연도> <제품명>」으로 나온다 */
  copyright: env.VITE_COPYRIGHT || '',
  /** 승계 고지 — MIT 조건상 유지한다. 임의로 비우지 않는다 */
  upstream: 'RAPID v1.0.29 · MIT · Y. Park, KRIBB',
} as const
