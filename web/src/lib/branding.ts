/**
 * Deployment-specific naming.
 *
 * foldfront is the product; whose instance it is, is configuration. No
 * organisation's name is written into a screen. Values are injected at build
 * time from .env - see web/.env.example.
 */

const env = import.meta.env

export const branding = {
  /** Product name, at the left of the header. */
  product: env.VITE_PRODUCT_NAME || 'foldfront',
  /** One line saying what it is; used as the header title. */
  title: env.VITE_APP_TITLE || '단백질 설계 자동화 플랫폼',
  /** Operating organisation. Left empty, the slot is omitted entirely. */
  organization: env.VITE_ORG_NAME || '',
  /** Footer copyright. Empty falls back to "(c) <year> <product>". */
  copyright: env.VITE_COPYRIGHT || '',
  /** Attribution. MIT requires the notice; this is not configurable. */
  upstream: 'RAPID v1.0.29 · MIT · Y. Park, KRIBB',
} as const
