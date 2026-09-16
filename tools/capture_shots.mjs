#!/usr/bin/env node
/**
 * 콘솔 화면 캡처.
 *
 * 문서가 docs/shots/ 의 캡처를 인용한다. 손으로 찍지 않고
 * 스크립트로 다시 뜰 수 있게 둔다 — 화면이 바뀌면 이 명령 한 줄로 갱신한다.
 *
 * 준비
 *   docker compose -f platform/docker-compose.yml up -d
 *   uv run python -m foldfront.cli seed
 *   uv run python -m foldfront.cli demo 6
 *   uv run uvicorn foldfront.api.app:app --port 18090
 *   cd web && npm run dev
 *   node tools/capture_shots.mjs
 *
 * ★ CDP(Page.captureScreenshot)를 쓰지 않는다. Chrome 153 headless 에서
 *   합성 프레임이 올라오지 않아 응답이 돌아오지 않았다(--disable-gpu 제거·
 *   fromSurface:false·swiftshader 모두 동일). Chrome 내장 --screenshot 은 정상이다.
 */

import { spawn } from 'node:child_process'
import { mkdir, rm, stat } from 'node:fs/promises'
import { setTimeout as sleep } from 'node:timers/promises'
import { resolve } from 'node:path'

const BASE = process.env.SHOT_BASE ?? 'http://localhost:5173'
const OUT = process.env.SHOT_OUT ?? 'docs/shots'
const CHROME =
  process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

//  지면이 본체다. 인쇄에서 뭉개지지 않게 2배로 찍는다
const WIDTH = 1440
const HEIGHT = 900
const SCALE = 2

const SHOTS = [
  { file: 'setup.png', path: '/setup', budget: 5000 },
  { file: 'studio.png', path: '/studio', budget: 9000 },
  { file: 'monitor.png', path: '/monitor', budget: 6000 },
  //  구조 2종을 소프트웨어로 그린다. 다른 화면보다 여유를 준다
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
      //  ★ --disable-gpu 를 쓰면 WebGL 컨텍스트가 없어 3Dmol 이 빈 화면으로 찍힌다.
      //  범례까지 나오므로 성공한 것처럼 보인다 — 소프트웨어 렌더러를 명시해야 구조가 그려진다.
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
  //  서버가 떠 있는지 먼저 본다. 빈 화면을 찍고 성공했다고 말하지 않는다
  const probe = await fetch(BASE).catch(() => null)
  if (!probe?.ok) throw new Error(`콘솔에 닿지 못했다 — ${BASE}`)
  const health = await fetch(`${BASE}/healthz`).then((r) => r.json()).catch(() => null)
  if (health?.status !== 'ok') {
    throw new Error('API 가 정상이 아니다 — 상태 표시등이 「점검」으로 찍힌다')
  }

  await mkdir(OUT, { recursive: true })

  //  인자를 주면 그 화면만 다시 찍는다 — 한 장 고치자고 여섯 장을 돌리지 않는다
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
