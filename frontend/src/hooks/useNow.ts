import { useEffect, useState } from 'react'

/** Current time, updated on each wall-clock boundary of `intervalMs` (default: every second). */
export function useNow(intervalMs = 1_000): Date {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    let timeoutId = 0
    const scheduleNextTick = () => {
      timeoutId = window.setTimeout(() => {
        setNow(new Date())
        scheduleNextTick()
      }, intervalMs - (Date.now() % intervalMs))
    }
    scheduleNextTick()
    return () => window.clearTimeout(timeoutId)
  }, [intervalMs])

  return now
}
