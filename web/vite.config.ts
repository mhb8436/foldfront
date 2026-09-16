import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'
import { execSync } from 'node:child_process'
import pkg from './package.json' with { type: 'json' }

//  푸터에 판번호와 기준 커밋을 노출한다. 배포본이 어느 시점인지 화면에서 바로 확인한다
const commit = (() => {
  try {
    return execSync('git rev-parse --short HEAD', { stdio: ['ignore', 'pipe', 'ignore'] })
      .toString()
      .trim()
  } catch {
    return 'unknown'
  }
})()

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    __APP_COMMIT__: JSON.stringify(commit),
  },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    //  콘솔은 /healthz 를 직접 부른다. /api 만 넘기면 개발 중 상태 표시등이 늘 「점검」이 된다
    proxy: {
      '/api': 'http://127.0.0.1:18090',
      '/healthz': 'http://127.0.0.1:18090',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/setupTests.ts'],
  },
})
