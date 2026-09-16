import { expect, test } from '@playwright/test'
import { FASTA, mongo, snap, startRun, waitForRunStatus } from './helpers'

/**
 * Forking a run and starting the fork.
 *
 * A fork is created waiting. The monitor has to open it and offer 시작, and
 * starting it has to run the stage it was forked at - and only that.
 */
test('fork → 시작: 갈라진 단계부터 다시 돈다', async ({ page }) => {
  const runId = await startRun(page, FASTA)
  const row = await waitForRunStatus(page, runId, '성공')
  await row.click()
  await expect(page.getByText(`실행 상세 — ${runId}`)).toBeVisible()

  await page.getByRole('button', { name: '여기서 fork' }).first().click()
  //  The fork opened, waiting, and says where it came from
  const detail = page.getByText(/실행 상세 — run-/)
  await expect(detail).not.toHaveText(new RegExp(runId))
  const childId = (await detail.textContent())!.match(/run-[0-9a-f]+/)![0]
  await expect(page.getByText(new RegExp(`${runId}.*단계에서 갈라진`))).toBeVisible()
  await expect(page.getByRole('button', { name: '시작' })).toBeVisible()
  await snap(page, 'fork-01-waiting')

  await page.getByRole('button', { name: '시작' }).click()
  await expect(page.getByText(/단계부터 다시 시작했습니다/)).toBeVisible({ timeout: 10_000 })
  await waitForRunStatus(page, childId, '성공')
  await snap(page, 'fork-02-started')

  for (const id of [runId, childId]) {
    mongo(`db.runs.deleteMany({run_id:'${id}'}); db.jobs.deleteMany({run_id:'${id}'}); db.run_events.deleteMany({run_id:'${id}'});`)
  }
})
