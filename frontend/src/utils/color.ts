export interface Rgb {
  r: number
  g: number
  b: number
}

const FALLBACK: Rgb = { r: 0, g: 0, b: 0 }

/** Parses a `#rrggbb` token value. */
export function hexToRgb(hex: string): Rgb {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!match?.[1]) return FALLBACK
  const value = Number.parseInt(match[1], 16)
  return { r: (value >> 16) & 255, g: (value >> 8) & 255, b: value & 255 }
}

export function rgba({ r, g, b }: Rgb, alpha: number): string {
  return `rgba(${r}, ${g}, ${b}, ${Math.min(1, Math.max(0, alpha)).toFixed(3)})`
}

/** Reads a design-token color (e.g. `accent-500`) from the theme's CSS variables. */
export function readThemeColor(token: string): Rgb {
  return hexToRgb(getComputedStyle(document.documentElement).getPropertyValue(`--color-${token}`))
}
