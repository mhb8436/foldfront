import { expect, test } from '@playwright/test'
import { FASTA, mongo, snap, startRun, waitForRunStatus } from './helpers'

/**
 * The run that stops moving, and the button that brings it back.
 *
 * Reconciliation was proven through the API. This is the person's side of
 * it: the bell says a run has stalled, 되살리기 is on that row, pressing it
 * re-queues the lost work, and the worker finishes what it was owed.
 */
test('멈춘 실행 되살리기: 알림 → 되살리기 → 성공', async ({ page }) => {
  const runId = await startRun(page, FASTA)
  await waitForRunStatus(page, runId, '성공')

  //  Make it the run seen in the real database: still running, a stage
  //  pending, no job at all, untouched for an hour.
  mongo(`
    db.runs.updateOne({run_id:'${runId}'}, {$set: {status:'running', finished_at:null, 'stages.0.status':'pending',
      updated_at: new Date(Date.now() - 3600*1000)}});
    db.jobs.deleteMany({run_id:'${runId}'});`)

  await page.goto('/monitor')
  await page.reload()
  await page.getByRole('button', { name: /^알림/ }).click()
  const menu = page.getByRole('menu', { name: '알림' })
  const row = menu.locator('div', { hasText: runId }).filter({ has: page.getByRole('button', { name: '되살리기' }) }).first()
  await expect(menu.getByText('실행이 멈춘 듯합니다').first()).toBeVisible()
  await snap(page, 'repair-01-stalled-notice')

  await menu.getByRole('button', { name: '되살리기' }).first().click()
  //  The notice goes as soon as the run moves again
  await expect(menu.getByText(new RegExp(runId))).toHaveCount(0, { timeout: 15_000 })
  await page.keyboard.press('Escape')

  await waitForRunStatus(page, runId, '성공')
  await snap(page, 'repair-02-recovered')
  void row

  mongo(`db.runs.deleteMany({run_id:'${runId}'}); db.jobs.deleteMany({run_id:'${runId}'}); db.run_events.deleteMany({run_id:'${runId}'});`)
})
