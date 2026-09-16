import { execSync } from 'node:child_process'
import { expect, type Page } from '@playwright/test'

/**
 * What the scenarios share.
 *
 * mongo() runs a script against the demo database through the container.
 * Scenarios use it for what the API deliberately does not offer - deleting a
 * job, backdating a run, changing the signed-in account's roles - and always
 * put back what they changed.
 */

export const STAMP = process.env.E2E_STAMP ?? String(Date.now()).slice(-6)

export const FASTA = `>lys-wt
MKALIVLGLVLLSVTVQGKVFERCELARTLKRLGMDGYRGISLANWMCLAKWESGYNTRATNYNAGDRSTDYGIFQINSRYWCNDGKTPGAVNACHLSCSALLQDNIADAVACAKRVVRDPQGIRAWVAWRNRCQNRDVRQYVQGCGV`

export function mongo(js: string): string {
  //  Through stdin, not --eval: the shell would expand $set and friends.
  return execSync('docker exec -i foldfront-mongo mongosh foldfront --quiet', {
    encoding: 'utf8',
    input: js,
  }).trim()
}

let shot = 0
export async function snap(page: Page, name: string) {
  shot += 1
  await page.screenshot({ path: `../docs/e2e/${name}.png`, fullPage: true })
}

/** Everything a scenario made, found by its stamp, removed. */
export function cleanup(stamp = STAMP) {
  mongo(`
    const projs = db.projects.find({name: /E2E .*${stamp}/}).toArray().map(p => p.project_id);
    const wfs = db.workflows.find({workflow_id: /^wf-e2e-.*${stamp}/}).toArray().map(w => w.workflow_id);
    const runs = db.runs.find({$or: [{project_id: {$in: projs}}, {workflow_id: {$in: wfs}}]}).toArray().map(r => r.run_id);
    db.rounds.deleteMany({project_id: {$in: projs}}); db.projects.deleteMany({project_id: {$in: projs}});
    db.workflows.deleteMany({workflow_id: {$in: wfs}});
    db.runs.deleteMany({run_id: {$in: runs}}); db.jobs.deleteMany({run_id: {$in: runs}}); db.run_events.deleteMany({run_id: {$in: runs}});
  `)
}

/** Start a run of the real soluprot workflow from Setup, return its id. */
export async function startRun(page: Page, fasta: string, workflowId = 'wf-soluprot-real'): Promise<string> {
  await page.goto('/setup')
  await page.getByLabel('실행할 워크플로').selectOption(workflowId)
  await page.getByRole('textbox', { name: '대상 서열' }).fill(fasta)
  await page.getByRole('button', { name: '실행', exact: true }).click()
  const started = page.getByText(/실행을 시작했습니다 — run-/)
  await expect(started).toBeVisible()
  return (await started.textContent())!.match(/run-[0-9a-f]+/)![0]
}

export async function waitForRunStatus(page: Page, runId: string, status: string, timeout = 90_000) {
  await page.goto('/monitor')
  const row = page.getByRole('row', { name: new RegExp(runId) })
  await expect(row).toBeVisible()
  await expect(row.getByText(status, { exact: true })).toBeVisible({ timeout })
  return row
}
