import { expect, test } from '@playwright/test'
import { STAMP, cleanup, snap } from './helpers'

/**
 * Drawing on the canvas.
 *
 * Adding a node, dragging an edge from one handle to another, seeing the
 * check panel notice what is missing, fixing it in the overlay panel, and
 * saving. None of this is reachable from a unit test.
 */
test('스튜디오: 노드 추가 → 간선 끌어 잇기 → 점검 → 편집 → 저장', async ({ page }) => {
  await page.goto('/studio')
  await page.getByLabel('불러오기').selectOption('wf-soluprot-real')
  await expect(page.getByText(/노드 1 · 간선 0/)).toBeVisible()

  await page.getByRole('button', { name: '모델', exact: true }).click()
  await expect(page.getByText(/노드 2 · 간선 0/)).toBeVisible()
  //  A new model node has no model, and the editor opened on it to say so
  await expect(page.getByRole('complementary', { name: /편집/ })).toBeVisible()
  await expect(page.getByText('모델 미지정').first()).toBeVisible()
  await page.keyboard.press('Escape')
  //  Bring both nodes into view: 화면 맞춤 lives on the canvas's right-click menu
  await page.locator('.react-flow__pane').click({ button: 'right', position: { x: 40, y: 40 } })
  await page.getByRole('menuitem', { name: '화면 맞춤' }).click()

  //  Drag from the existing node's right handle to the new node's left handle
  const nodes = page.locator('.react-flow__node')
  await expect(nodes).toHaveCount(2)
  const from = nodes.nth(0).locator('.react-flow__handle-right')
  const to = nodes.nth(1).locator('.react-flow__handle-left')
  const a = (await from.boundingBox())!
  const b = (await to.boundingBox())!
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
  await page.mouse.down()
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 })
  await page.mouse.up()
  await expect(page.getByText(/노드 2 · 간선 1/)).toBeVisible()
  await snap(page, 'studio-01-edge-drawn')

  //  The check panel names what is missing, and 고치기 opens the editor there
  await expect(page.getByText(/점검.*1건 남음|1건 남음/).first()).toBeVisible()
  await page.getByRole('button', { name: '고치기' }).first().click()
  await page.getByLabel('실행할 모델').selectOption('soluprot')
  await expect(page.getByText('점검', { exact: true })).toHaveCount(0)
  await snap(page, 'studio-02-fixed')

  await page.getByLabel('식별자').fill(`wf-e2e-studio-${STAMP}`)
  await page.getByLabel('이름').fill(`E2E 스튜디오 ${STAMP}`)
  await page.getByRole('button', { name: '저장' }).click()
  await expect(page.getByText(/저장했습니다 — v1 · 실행 층 2개/)).toBeVisible()
  await snap(page, 'studio-03-saved')

  cleanup()
})
