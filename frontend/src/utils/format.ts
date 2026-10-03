/** HH:mm:ss in UTC — the project's timestamp convention. */
export function formatUtcTime(date: Date): string {
  return date.toISOString().slice(11, 19)
}

/** Host (and port) portion of a URL, falling back to the raw value if it cannot be parsed. */
export function formatHost(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/** Human-readable size using binary units, e.g. 2.4 MB. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB'] as const
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = value >= 100 || Number.isInteger(value) ? 0 : 1
  return `${value.toFixed(digits)} ${units[unit]}`
}

/** Thousands-separated integer, e.g. 1,234. */
export function formatCount(value: number): string {
  return value.toLocaleString('en-US')
}

/** Readable local-time stamp, e.g. "03 Oct 2026, 02:45 PM". Returns the input unchanged if it is not a valid date. */
export function formatLocalDateTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const day = String(date.getDate()).padStart(2, '0')
  const month = date.toLocaleString('en-US', { month: 'short' })
  const time = date.toLocaleString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true })
  return `${day} ${month} ${date.getFullYear()}, ${time}`
}

/** Exact UTC instant for tooltips, e.g. "2026-10-03 08:15:30 UTC". */
export function formatUtcDateTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return `${date.toISOString().slice(0, 10)} ${formatUtcTime(date)} UTC`
}
