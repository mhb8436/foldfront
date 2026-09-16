import { useCallback, useEffect, useState } from 'react'

/** Loading and error state for one fetch, so no screen repeats it. */
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

/** Refetch on an interval. This is how run monitoring keeps up. */
export function usePolling<T>(fn: () => Promise<T>, intervalMs: number, deps: unknown[] = []) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (intervalMs <= 0) return
    const id = setInterval(() => setTick((t) => t + 1), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return useAsync(fn, [...deps, tick])
}
