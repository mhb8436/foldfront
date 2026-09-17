import { expect, test } from '@playwright/test'
import { FASTA, mongo, snap, startRun, waitForRunStatus } from './helpers'

/**
 * What a reader sees.
 *
 * Hiding a control is a courtesy, not the check - the server enforces it -
 * but a reader who is shown a button that answers 403 has been shown a
 * broken product. So: no 저장, no 실행, no 되살리기, no 운영 or 이용자 in the
 * navigation. The signed-in account is demoted for the duration and put back.
 */
test('조회자 화면: 쓰기 단추가 없다', async ({ page }) => {
  //  A run to look at, made while still an operator, so the monitor check
  //  below always has a row and cannot pass by having nothing to assert on.
  const runId = await startRun(page, FASTA)
  await waitForRunStatus(page, runId, '성공')
  mongo(`db.users.updateOne({user_id:'dev'}, {$set: {roles: ['viewer']}})`)
  try {
    await page.goto('/studio')
    await expect(page.getByText('조회자')).toBeVisible()
    await expect(page.getByRole('button', { name: '저장' })).toBeDisabled()
    await expect(page.getByRole('link', { name: '운영' })).toHaveCount(0)
    await expect(page.getByRole('link', { name: '이용자' })).toHaveCount(0)
    await snap(page, 'viewer-01-studio')

    await page.goto('/setup')
    await expect(page.getByRole('button', { name: '실행', exact: true })).toBeDisabled()
    await expect(page.getByRole('textbox', { name: '대상 서열' })).toBeDisabled()
    await snap(page, 'viewer-02-setup')

    //  Hiding is the courtesy; refusing is the check. Both must hold.
    const refused = await page.request.post('/api/v1/projects', { data: { project_id: '', name: 'E2E 침입', archived: false, tags: [] } })
    expect(refused.status()).toBe(403)

    await page.goto('/monitor')
    const row = page.getByRole('row', { name: new RegExp(runId) })
    await expect(row).toBeVisible()
    await row.click()
    await expect(page.getByText(`실행 상세 — ${runId}`)).toBeVisible()
    await expect(page.getByRole('button', { name: '여기서 fork' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '정합성 점검' })).toHaveCount(0)

    await page.goto('/dashboard')
    await expect(page.getByRole('link', { name: /실행 시작/ })).toHaveCount(0)
    //  Approvals and the auth warning are for an operator: a reader is not told
    await page.getByRole('button', { name: /^알림/ }).click()
    const menu = page.getByRole('menu', { name: '알림' })
    await expect(menu.getByText('모델 승인 대기')).toHaveCount(0)
    await expect(menu.getByText('인증이 꺼져 있습니다')).toHaveCount(0)
    await snap(page, 'viewer-03-notices')
  } finally {
    mongo(`db.users.updateOne({user_id:'dev'}, {$set: {roles: ['admin']}})`)
    mongo(`db.runs.deleteMany({run_id:'${runId}'}); db.jobs.deleteMany({run_id:'${runId}'}); db.run_events.deleteMany({run_id:'${runId}'});`)
  }
})
