import { expect, test } from '@playwright/test'
import { mongo, snap, waitForRunStatus, startRun } from './helpers'

/**
 * The run that fails.
 *
 * Half of a product is what it shows when things go wrong. A header with no
 * sequence after it is a real mistake a person makes; the run must fail, the
 * bell must say so, and the run's own screen must say why.
 */
test('실패 경로: 잘못된 서열 → 실패 → 알림 → 원인', async ({ page }) => {
  const runId = await startRun(page, '>empty-header-only\n')

  const row = await waitForRunStatus(page, runId, '실패')
  await row.click()
  await expect(page.getByText(`실행 상세 — ${runId}`)).toBeVisible()
  //  The cause is on the run, not hidden in a log
  await expect(page.getByText(/FASTA|서열/).first()).toBeVisible()
  await snap(page, 'fail-01-run-detail')

  //  The bell polls every 30s; the failure is newer than its last look.
  await page.reload()
  await page.getByRole('button', { name: /^알림/ }).click()
  const menu = page.getByRole('menu', { name: '알림' })
  await expect(menu.getByText(new RegExp(runId))).toBeVisible()
  await expect(menu.getByText('실행이 실패했습니다').first()).toBeVisible()
  await snap(page, 'fail-02-notice')
  //  Failed runs have nothing to bring back
  await expect(menu.getByRole('button', { name: '되살리기' })).toHaveCount(0)

  mongo(`db.runs.deleteMany({run_id:'${runId}'}); db.jobs.deleteMany({run_id:'${runId}'}); db.run_events.deleteMany({run_id:'${runId}'});`)
})
