import { useEffect, useRef } from 'react'
import { useFinePointer, usePrefersReducedMotion } from '../../hooks/useMediaQuery'
import { subscribeToPointer } from '../../utils/pointerTracker'

/**
 * Ambient light that trails the cursor behind the translucent surfaces.
 * Moved with compositor-only transforms; not rendered for touch input or
 * when the user prefers reduced motion. The native cursor is left untouched.
 */
export function CursorLight() {
  const ref = useRef<HTMLDivElement>(null)
  const finePointer = useFinePointer()
  const reducedMotion = usePrefersReducedMotion()
  const enabled = finePointer && !reducedMotion

  useEffect(() => {
    const element = ref.current
    if (!enabled || !element) return undefined

    return subscribeToPointer(({ x, y, inside }) => {
      element.style.transform = `translate3d(${x}px, ${y}px, 0) translate(-50%, -50%)`
      element.style.opacity = inside ? '1' : '0'
    })
  }, [enabled])

  if (!enabled) return null
  return <div ref={ref} aria-hidden="true" className="cursor-light" />
}
