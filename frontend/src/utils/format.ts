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
