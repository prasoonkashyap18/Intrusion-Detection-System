import { useEffect, type RefObject } from 'react'
import { subscribeToPointer } from '../utils/pointerTracker'
import { useFinePointer, usePrefersReducedMotion } from './useMediaQuery'

/**
 * Writes the viewport-normalized pointer position (-1…1) to --parallax-x /
 * --parallax-y on the element, for depth layers that drift with the cursor.
 * Smoothing comes from a CSS transition on the consuming element.
 */
export function usePointerParallax(ref: RefObject<HTMLElement | null>): void {
  const finePointer = useFinePointer()
  const reducedMotion = usePrefersReducedMotion()
  const enabled = finePointer && !reducedMotion

  useEffect(() => {
    const element = ref.current
    if (!enabled || !element) return undefined

    const unsubscribe = subscribeToPointer(({ nx, ny, inside }) => {
      element.style.setProperty('--parallax-x', (inside ? nx : 0).toFixed(3))
      element.style.setProperty('--parallax-y', (inside ? ny : 0).toFixed(3))
    })

    return () => {
      unsubscribe()
      element.style.removeProperty('--parallax-x')
      element.style.removeProperty('--parallax-y')
    }
  }, [ref, enabled])
}
