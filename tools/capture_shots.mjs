#!/usr/bin/env node
/**
 * Console screenshots.
 *
 * Documentation cites the images in docs/shots, so they are taken by script
 * rather than by hand: when a screen changes, this one command refreshes them.
 *
 * Prerequisites
 *   docker compose -f platform/docker-compose.yml up -d
 *   uv run python -m foldfront.cli seed
 *   uv run python -m foldfront.cli demo 6
 *   uv run uvicorn foldfront.api.app:app --port 18090
 *   cd web && npm run dev
 *   node tools/capture_shots.mjs
 *
 * Not CDP. Page.captureScreenshot never returns on Chrome 153 headless here -
 * removing --disable-gpu, fromSurface:false and swiftshader all behave the
 * same way. Chrome's own --screenshot flag works.
 */

import { spawn } from 'node:child_process'
import { mkdir, rm, stat } from 'node:fs/promises'
import { setTimeout as sleep } from 'node:timers/promises'
import { resolve } from 'node:path'

const BASE = process.env.SHOT_BASE ?? 'http://localhost:5173'
const OUT = process.env.SHOT_OUT ?? 'docs/shots'
const CHROME =
  process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

//  These are read on paper, so capture at 2x rather than let print blur them
const WIDTH = 1440
const HEIGHT = 900
const SCALE = 2

const SHOTS = [
  { file: 'setup.png', path: '/setup', budget: 5000 },
  { file: 'studio.png', path: '/studio', budget: 9000 },
  { file: 'monitor.png', path: '/monitor', budget: 6000 },
  //  Two structures, rendered in software. Slower than the other screens.
  { file: 'analyze.png', path: '/analyze', budget: 14000 },
  { file: 'models.png', path: '/models', budget: 6000 },
  { file: 'operations.png', path: '/operations', budget: 6000 },
]

async function capture(shot) {
  const out = resolve(OUT, shot.file)
  const profile = `/tmp/foldfront-shot-${process.pid}`
  await rm(out, { force: true })
  await rm(profile, { recursive: true, force: true })

  const child = spawn(
    CHROME,
    [
      '--headless',
      //  Do not add --disable-gpu. Without a WebGL context 3Dmol draws
      //  nothing, while the spinner still clears and the legend still
      //  renders - the failure looks exactly like success.
      '--use-gl=angle',
      '--use-angle=swiftshader',
      '--enable-unsafe-swiftshader',
      '--no-first-run',
      '--no-default-browser-check',
      '--hide-scrollbars',
      '--default-background-color=FFFFFFFF',
      `--user-data-dir=${profile}`,
      `--window-size=${WIDTH},${HEIGHT}`,
      `--force-device-scale-factor=${SCALE}`,
      `--virtual-time-budget=${shot.budget}`,
      `--screenshot=${out}`,
      BASE + shot.path,
    ],
    { stdio: 'ignore' },
  )

  const done = new Promise((res) => child.once('exit', res))
  const timedOut = await Promise.race([done.then(() => false), sleep(45000).then(() => true)])
  if (timedOut) child.kill('SIGKILL')
  await rm(profile, { recursive: true, force: true })

  const info = await stat(out).catch(() => null)
  if (!info?.size) throw new Error(`${shot.file} 을 만들지 못했다`)
  return info.size
}

async function main() {
  //  Check the servers first, rather than capture blank screens and report success
  const probe = await fetch(BASE).catch(() => null)
  if (!probe?.ok) throw new Error(`콘솔에 닿지 못했다 — ${BASE}`)
  const health = await fetch(`${BASE}/healthz`).then((r) => r.json()).catch(() => null)
  if (health?.status !== 'ok') {
    throw new Error('API 가 정상이 아니다 — 상태 표시등이 「점검」으로 찍힌다')
  }

  await mkdir(OUT, { recursive: true })

  //  An argument narrows it to one screen; re-running all six to fix one is waste
  const only = process.argv.slice(2)
  const targets = only.length
    ? SHOTS.filter((s) => only.some((o) => s.file.startsWith(o) || s.path === `/${o}`))
    : SHOTS
  if (targets.length === 0) throw new Error(`해당하는 화면이 없다 — ${only.join(' ')}`)

  for (const shot of targets) {
    const size = await capture(shot)
    console.log(`  ${shot.file.padEnd(16)} ${shot.path.padEnd(12)} ${Math.round(size / 1024)} KB`)
  }
  console.log(`\n${targets.length}장 · ${WIDTH}x${HEIGHT} @${SCALE}x · ${OUT}/`)
}

main().catch((e) => {
  console.error('캡처 실패 —', e.message)
  process.exit(1)
})
