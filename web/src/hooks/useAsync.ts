import { useCallback, useEffect, useState } from 'react'

/** 비동기 조회 상태를 한 곳에서 다룬다. 화면마다 로딩·오류 처리를 되풀이하지 않는다. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const run = useCallback(() => {
    let alive = true
    setLoading(true)
    fn()
      .then((v) => alive && (setData(v), setError(null)))
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, deps)

  useEffect(() => run(), [run])

  return { data, error, loading, reload: run }
}

/** 일정 간격으로 다시 조회한다. Monitor 화면이 진행 상태를 따라간다. */
export function usePolling<T>(fn: () => Promise<T>, intervalMs: number, deps: unknown[] = []) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (intervalMs <= 0) return
    const id = setInterval(() => setTick((t) => t + 1), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return useAsync(fn, [...deps, tick])
}
