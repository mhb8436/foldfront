#!/usr/bin/env node
/**
 * The README's moving demo.
 *
 * Recorded by script for the same reason docs/shots are: when a screen
 * changes, one command takes it again. Each scene is its own .webm, then the
 * scenes are joined and turned into one GIF.
 *
 * Prerequisites - the same stack as capture_shots.mjs, plus a worker so the
 * run started on screen actually moves. A mock worker with no delay finishes
 * before the monitor's first refresh, so give each stage a pause:
 *   docker compose -f platform/docker-compose.yml up -d
 *   uv run uvicorn foldfront.api.app:app --port 18090
 *   uv run python -c "import asyncio, functools; from foldfront import cli; \
 *     from foldfront.engine.adapters import AdapterRegistry, MockAdapter; \
 *     cli.AdapterRegistry = functools.partial(AdapterRegistry, mock_adapter=MockAdapter(delay=1.2)); \
 *     asyncio.run(cli.cmd_worker(mock=True))"
 *   cd web && npm run dev
 *   node tools/record_demo.mjs            all scenes
 *   node tools/record_demo.mjs 3-monitor  one scene
 *
 * The run it starts is a mock run. Its numbers are not results, and the
 * README says so under the image.
 */

import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { mkdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'

//  Playwright is a dev dependency of the console, not of the repository root
const require = createRequire(resolve('web/package.json'))
const { chromium } = require('playwright')

const BASE = process.env.DEMO_BASE ?? 'http://localhost:5173'
const REC = resolve(process.env.DEMO_REC ?? 'web/test-results/demo')
const OUT = resolve(process.env.DEMO_OUT ?? 'assets/foldfront-demo.gif')

const VIEW = { width: 1280, height: 800 }

//  Two runs that both left a predicted structure, so the comparison has
//  something to draw. Overridable because a fresh database has other ids.
const LEFT = process.env.DEMO_LEFT ?? 'run-bb4b31bcdcec'
const RIGHT = process.env.DEMO_RIGHT ?? 'run-8844fed28b19'

//  A cursor the recording can see. Headless video has none. Zinc, because the
//  only colour the console allows itself is run status.
const CURSOR = `(() => {
  const make = () => {
    if (document.getElementById('__cur')) return
    const c = document.createElement('div')
    c.id = '__cur'
    Object.assign(c.style, {
      position: 'fixed', zIndex: 2147483647, width: '18px', height: '18px',
      borderRadius: '50%', background: 'rgba(24,24,27,.85)', border: '2px solid #fff',
      boxShadow: '0 0 0 2px rgba(24,24,27,.35)', pointerEvents: 'none',
      left: '-60px', top: '-60px', transform: 'translate(-50%,-50%)', transition: 'transform .12s',
    })
    document.body.appendChild(c)
  }
  if (document.body) make(); else document.addEventListener('DOMContentLoaded', make)
  const cur = () => document.getElementById('__cur')
  document.addEventListener('mousemove', (e) => {
    const c = cur(); if (c) { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px' }
  }, true)
  document.addEventListener('mousedown', () => { const c = cur(); if (c) c.style.transform = 'translate(-50%,-50%) scale(.55)' }, true)
  document.addEventListener('mouseup', () => { const c = cur(); if (c) c.style.transform = 'translate(-50%,-50%) scale(1)' }, true)
})()`

//  Where the pointer was last, so the next move starts from there rather than
//  jumping from the corner
let pointer = { x: VIEW.width / 2, y: VIEW.height / 2 }

async function moveTo(page, locator) {
  const box = await locator.first().boundingBox()
  if (!box) throw new Error('보이지 않는 요소로 이동하려 했다')
  pointer = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
  await page.mouse.move(pointer.x, pointer.y, { steps: 24 })
  await sleep(200)
}

async function click(page, locator, after = 700) {
  await moveTo(page, locator)
  await page.mouse.down()
  await sleep(80)
  await page.mouse.up()
  await sleep(after)
}

async function jump(page, dy) {
  await page.evaluate((y) => window.scrollBy({ top: y, behavior: 'instant' }), dy)
}

//  A 3Dmol viewer needs WebGL; without swiftshader it draws nothing and the
//  legend still renders, so the failure looks like success
const browser = await chromium.launch({
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
})

//  Seconds from the start of each clip to its first frame worth showing. The
//  video starts when the context opens, well before the page has painted;
//  each scene calls ready() once its opening screen is drawn, and everything
//  before that is cut when the clips are joined.
const lead = {}

async function scene(name, fn) {
  const opened = Date.now()
  const ready = () => (lead[name] = (Date.now() - opened) / 1000)
  const ctx = await browser.newContext({
    viewport: VIEW,
    locale: 'ko-KR',
    recordVideo: { dir: REC, size: VIEW },
  })
  await ctx.addInitScript(CURSOR)
  const page = await ctx.newPage()
  //  Put the pointer back where the last scene left it
  await page.mouse.move(pointer.x, pointer.y)
  try {
    await fn(page, ready)
    console.log(`  녹화 ${name}`)
  } catch (e) {
    console.log(`  실패 ${name} — ${e.message.split('\n')[0]}`)
  }
  await sleep(500)
  const video = page.video()
  await ctx.close()
  await rename(await video.path(), `${REC}/${name}.webm`)
}

//  A page's first paint is a skeleton. Scenes open on a screen already drawn.
async function open(page, path, settle = 1500) {
  await page.goto(BASE + path, { waitUntil: 'networkidle' })
  await sleep(settle)
}

async function runStatus(runId) {
  const run = await fetch(`${BASE}/api/v1/runs/${runId}`).then((r) => r.json()).catch(() => null)
  return run?.status
}

const SCENES = {
  //  The product in one screen: a workflow is a graph, and nodes differ by
  //  shape - model, condition, parallel split
  '1-studio': async (page, ready) => {
    await open(page, '/studio?wf=binding-prediction', 2500)
    ready()
    await sleep(900)
    const gate = page.locator('.react-flow__node').filter({ hasText: '용해도 통과' })
    await moveTo(page, gate)
    await sleep(300)
    await gate.first().dblclick()
    await sleep(2800)
    await page.keyboard.press('Escape')
    await sleep(600)
  },

  //  Pick a workflow, paste a sequence, check it, start it
  '2-setup': async (page, ready) => {
    await open(page, '/setup')
    ready()
    await sleep(500)
    await click(page, page.locator('#workflow'), 300)
    await page.locator('#workflow').selectOption('triage-pipeline')
    await sleep(700)
    const fasta = page.locator('textarea').first()
    await click(page, fasta, 200)
    await page.keyboard.type('>lysozyme\nKVFERCELARTLKRLGMDGYRGISLANWMCLAKWESGYNTRATNYNAGDRSTDYGIFQINSRYWCNDGKTPGAVNACHLSCSALLQDNIADAVACAKRVVRDPQGIRAWVAWRNRCQNRDVRQYVQGCGV', { delay: 4 })
    await sleep(500)
    await click(page, page.getByRole('button', { name: '점검' }), 900)
    //  The check's result lands below the fold. Jumped to rather than wheeled:
    //  the recorder catches a scroll half-composited and the frames tear.
    await jump(page, 240)
    await sleep(1400)
    await click(page, page.getByRole('button', { name: '실행', exact: true }), 500)
    //  The notice naming the new run lands at the top, above the fold
    await jump(page, -1000)
    await sleep(2000)
  },

  //  The run just started, moving stage by stage
  '3-monitor': async (page, ready) => {
    await open(page, '/monitor', 800)
    ready()
    await sleep(600)
    const row = page.locator('tbody tr').first()
    const runId = (await row.innerText()).match(/run-[0-9a-f]+/)?.[0]
    await click(page, row, 600)
    //  Stay on it until the run is done, asked of the API rather than read
    //  off the screen - the detail table has 성공 rows long before the end
    for (let i = 0; i < 40 && runId; i++) {
      if (['succeeded', 'failed', 'cancelled'].includes(await runStatus(runId))) break
      await sleep(500)
    }
    //  The screen refreshes every 4 seconds; let it catch up, then hold
    await click(page, page.getByRole('button', { name: '새로고침' }), 2400)
  },

  //  Two runs side by side: predicted structures and the stage metrics
  '4-analyze': async (page, ready) => {
    await open(page, `/analyze?left=${LEFT}&right=${RIGHT}`, 800)
    //  Jump, not scroll: a smooth scroll over two WebGL viewers tears in the
    //  recording. The jump happens before ready(), so it is cut.
    await page.getByText('구조 비교', { exact: true }).first().evaluate((el) =>
      window.scrollBy(0, el.getBoundingClientRect().top - 90),
    )
    await sleep(3500)
    ready()
    //  Turn the left structure by hand - it is a live viewer, not a picture
    const canvas = page.locator('canvas').first()
    await moveTo(page, canvas)
    await sleep(400)
    await page.mouse.down()
    for (let i = 1; i <= 30; i++) {
      await page.mouse.move(pointer.x + i * 5, pointer.y + Math.sin(i / 6) * 18)
      await sleep(30)
    }
    await page.mouse.up()
    await sleep(1400)
    //  No scrolling here. The WebGL viewers lag the page in the recording and
    //  leave a ghost of themselves; the stage table is already in view.
    await moveTo(page, page.locator('tbody tr').filter({ hasText: 'msa' }).first())
    await sleep(2200)
  },
}

function ffmpeg(...args) {
  execFileSync('ffmpeg', ['-v', 'error', '-y', ...args], { stdio: 'inherit' })
}

async function main() {
  const probe = await fetch(`${BASE}/healthz`).then((r) => r.json()).catch(() => null)
  if (probe?.status !== 'ok') throw new Error(`콘솔이나 API 에 닿지 못했다 — ${BASE}`)

  await mkdir(REC, { recursive: true })
  const only = process.argv.slice(2)
  const names = Object.keys(SCENES).filter((n) => !only.length || only.some((o) => n.startsWith(o)))
  //  Kept beside the clips, so re-taking one scene does not lose where the
  //  others start
  Object.assign(lead, JSON.parse(await readFile(`${REC}/lead.json`, 'utf8').catch(() => '{}')), {})
  for (const name of names) await scene(name, SCENES[name])
  await browser.close()
  await writeFile(`${REC}/lead.json`, JSON.stringify(lead, null, 1))

  //  Trim each clip, then join them. Re-encoded rather than stream-copied,
  //  because a cut inside a webm keyframe interval does not land where asked.
  const all = Object.keys(SCENES)
  const list = []
  for (const name of all) {
    const src = `${REC}/${name}.webm`
    if (!(await stat(src).catch(() => null))) continue
    const cut = `${REC}/${name}.cut.mp4`
    ffmpeg('-ss', String(lead[name] ?? 0), '-i', src, '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-r', '25', cut)
    list.push(`file '${cut}'`)
  }
  await writeFile(`${REC}/list.txt`, list.join('\n'))
  const joined = `${REC}/joined.mp4`
  ffmpeg('-f', 'concat', '-safe', '0', '-i', `${REC}/list.txt`, '-c', 'copy', joined)

  //  Two-pass palette. The console is nearly monochrome, so 128 colours hold
  //  it; the status dots and the structure are the only colour there is.
  await mkdir(resolve(OUT, '..'), { recursive: true })
  await rm(OUT, { force: true })
  ffmpeg(
    '-i', joined,
    '-vf',
    'fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle',
    OUT,
  )
  const size = (await stat(OUT)).size
  console.log(`\n${OUT} · ${(size / 1024 / 1024).toFixed(1)} MB`)
}

main().catch(async (e) => {
  console.error('녹화 실패 —', e.message)
  await browser.close().catch(() => {})
  process.exit(1)
})
