import { expect, test } from '@playwright/test'
import { STAMP, cleanup, snap } from './helpers'

/**
 * The whole process, once, on screen.
 *
 * 프로젝트 개설 → 회차 개설 → 워크플로 저장 → 서열 붙여넣기 → 실행 → 성공 확인
 * → 결과 분석에 보임 → 알림 → 운영 정합성 점검. Each step leaves a screenshot in
 * docs/e2e so the document can show what was seen, not describe it.
 *
 * Everything created carries STAMP, so a cleanup can find it.
 */

const PROJECT = `E2E 리소자임 ${STAMP}`
const WF_ID = `wf-e2e-${STAMP}`
const FASTA = `>lys-wt
MKALIVLGLVLLSVTVQGKVFERCELARTLKRLGMDGYRGISLANWMCLAKWESGYNTRATNYNAGDRSTDYGIFQINSRYWCNDGKTPGAVNACHLSCSALLQDNIADAVACAKRVVRDPQGIRAWVAWRNRCQNRDVRQYVQGCGV
>lys-d1
MKALIVLGLVLLSVTVQGKVFERCELARTLKRLGMDGYRGISLANWMCLAKWESGYNTRATNYNAGDRSTDYGIFQINSRYWCNDGKTPGAVNACHLSCSALLQDNIADAVACAKRVVRDPQGIRAWVAWRNRCQNRDVRQYVQGCGV`


test.describe.configure({ mode: 'serial' })

test('전 과정: 프로젝트 → 회차 → 워크플로 → 입력 → 실행 → 결과 → 알림 → 운영', async ({ page }) => {
  try {
  // ---------------------------------------------------------------- 1 프로젝트
  await page.goto('/projects')
  await expect(page.getByRole('heading', { name: '프로젝트' })).toBeVisible()
  await page.getByLabel('새 프로젝트 이름').fill(PROJECT)
  await page.getByRole('button', { name: '개설' }).first().click()
  await expect(page.getByText(/프로젝트를 만들었습니다/)).toBeVisible()
  //  The new project is selected, and the header says so.
  await expect(page.getByRole('button', { name: new RegExp(PROJECT) })).toBeVisible()
  const row = page.getByRole('row', { name: new RegExp(PROJECT) })
  await expect(row.getByText('선택됨')).toBeVisible()
  await snap(page, 'project-created')

  // ---------------------------------------------------------------- 2 회차
  await page.getByLabel(/1차 이름/).fill('')
  await page.getByLabel('목표').fill('기준선 — 용해도 통과율 측정')
  await page.getByRole('button', { name: '개설' }).nth(1).click()
  await expect(page.getByText(/회차를 열었습니다 — 1차 설계/)).toBeVisible()
  await expect(page.getByRole('row', { name: /1차 설계/ })).toBeVisible()
  await snap(page, 'round-opened')

  // ---------------------------------------------------------------- 3 워크플로
  await page.goto('/studio')
  await page.getByLabel('불러오기').selectOption('wf-soluprot-real')
  await expect(page.getByLabel('식별자')).toHaveValue('wf-soluprot-real')
  await page.getByLabel('식별자').fill(WF_ID)
  await page.getByLabel('이름').fill(`E2E 가용성 ${STAMP}`)
  await page.getByRole('button', { name: '저장' }).click()
  await expect(page.getByText(/저장했습니다 — v1/)).toBeVisible()
  //  점검 패널이 없어야 한다 — 빠진 것이 없으니
  await expect(page.getByText('점검', { exact: true })).toHaveCount(0)
  await snap(page, 'workflow-saved')

  // ---------------------------------------------------------------- 4 입력·실행
  await page.goto('/setup')
  await page.getByLabel('실행할 워크플로').selectOption(WF_ID)
  //  The latest round is the default; the project is named.
  await expect(page.getByLabel('기록할 회차')).toContainText('1차 설계')
  await page.getByRole('textbox', { name: '대상 서열' }).fill(FASTA)
  await expect(page.getByText(/서열 2개 · 148, 148 잔기/)).toBeVisible()
  await snap(page, 'setup-input')
  await page.getByRole('button', { name: '점검' }).click()
  await expect(page.getByText(/모든 모델을 해석했습니다/)).toBeVisible()
  await page.getByRole('button', { name: '실행', exact: true }).click()
  const started = page.getByText(/실행을 시작했습니다 — run-/)
  await expect(started).toBeVisible()
  const runId = (await started.textContent())!.match(/run-[0-9a-f]+/)![0]
  await expect(started).toContainText(`${PROJECT} · 1차`)
  await snap(page, 'run-started')

  // ---------------------------------------------------------------- 5 감시
  await page.goto('/monitor')
  const runRow = page.getByRole('row', { name: new RegExp(runId) })
  await expect(runRow).toBeVisible()
  //  A live worker is running; wait for it.
  await expect(runRow.getByText('성공')).toBeVisible({ timeout: 90_000 })
  await runRow.click()
  await expect(page.getByText(`실행 상세 — ${runId}`)).toBeVisible()
  await expect(page.getByText('soluprot').first()).toBeVisible()
  await snap(page, 'run-succeeded')

  // ---------------------------------------------------------------- 6 회차에 기록됨
  await page.goto('/projects')
  await expect(page.getByRole('row', { name: /1차 설계/ }).getByText('성공')).toBeVisible()
  await snap(page, 'round-has-run')

  // ---------------------------------------------------------------- 7 결과 분석
  await page.goto('/analyze')
  //  Runs are chosen from a select here; the option is what proves it is listed.
  await expect(page.locator(`option[value="${runId}"]`).first()).toBeAttached()
  await page.locator('select').first().selectOption(runId)
  await snap(page, 'analyze-lists-run')

  // ---------------------------------------------------------------- 8 알림
  await page.getByRole('button', { name: /^알림/ }).click()
  await expect(page.getByRole('menu', { name: '알림' })).toBeVisible()
  await snap(page, 'notices-open')
  await page.keyboard.press('Escape')

  // ---------------------------------------------------------------- 9 운영
  await page.goto('/operations')
  await page.getByRole('button', { name: '정합성 점검' }).click()
  await expect(page.getByText(/진행 중인 실행 \d+건/)).toBeVisible()
  await snap(page, 'operations-reconcile')

  // ---------------------------------------------------------------- 10 범위 전환
  await page.getByRole('button', { name: /^프로젝트/ }).click()
  await page.getByRole('menuitemradio', { name: /전체/ }).click()
  await page.goto('/dashboard')
  await expect(page.getByText(/전체 \d+건/)).toBeVisible()
  await snap(page, 'dashboard-whole')
  } finally {
    cleanup()
  }
})
