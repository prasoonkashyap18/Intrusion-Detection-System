import { useEffect, type RefObject } from 'react'
import { useFinePointer, usePrefersReducedMotion } from './useMediaQuery'

export type SurfaceInteraction = 'none' | 'spotlight' | 'tilt'

const MAX_TILT_DEG = 4

/**
 * Cursor-aware surface response without React re-renders: the local pointer
 * position and tilt angles are written as CSS custom properties
 * (--pointer-local-x/y, --tilt-rx/ry) consumed by the .panel-* styles.
 * Disabled for coarse/touch pointers; tilt is also disabled for users who
 * prefer reduced motion.
 */
export function useSurfaceInteraction(
  ref: RefObject<HTMLElement | null>,
  mode: SurfaceInteraction,
): void {
  const finePointer = useFinePointer()
  const reducedMotion = usePrefersReducedMotion()
  const tiltEnabled = mode === 'tilt' && !reducedMotion

  useEffect(() => {
    const element = ref.current
    if (!element || mode === 'none' || !finePointer) return undefined

    let bounds: DOMRect | null = null
    let frameId = 0
    let pointerX = 0
    let pointerY = 0

    const invalidateBounds = () => {
      bounds = null
    }

    const render = () => {
      frameId = 0
      bounds ??= element.getBoundingClientRect()
      const x = pointerX - bounds.left
      const y = pointerY - bounds.top
      element.style.setProperty('--pointer-local-x', `${x.toFixed(1)}px`)
      element.style.setProperty('--pointer-local-y', `${y.toFixed(1)}px`)
      if (tiltEnabled && bounds.width > 0 && bounds.height > 0) {
        const offsetX = x / bounds.width - 0.5
        const offsetY = y / bounds.height - 0.5
        element.style.setProperty('--tilt-rx', `${(-offsetY * 2 * MAX_TILT_DEG).toFixed(2)}deg`)
        element.style.setProperty('--tilt-ry', `${(offsetX * 2 * MAX_TILT_DEG).toFixed(2)}deg`)
      }
    }

    const handlePointerEnter = (event: PointerEvent) => {
      if (event.pointerType === 'touch') return
      element.dataset.pointerActive = ''
      window.addEventListener('scroll', invalidateBounds, { passive: true })
      window.addEventListener('resize', invalidateBounds)
    }

    const handlePointerMove = (event: PointerEvent) => {
      if (event.pointerType === 'touch') return
      pointerX = event.clientX
      pointerY = event.clientY
      if (!frameId) frameId = window.requestAnimationFrame(render)
    }

    const reset = () => {
      if (frameId) window.cancelAnimationFrame(frameId)
      frameId = 0
      bounds = null
      delete element.dataset.pointerActive
      element.style.removeProperty('--tilt-rx')
      element.style.removeProperty('--tilt-ry')
      window.removeEventListener('scroll', invalidateBounds)
      window.removeEventListener('resize', invalidateBounds)
    }

    element.addEventListener('pointerenter', handlePointerEnter)
    element.addEventListener('pointermove', handlePointerMove)
    element.addEventListener('pointerleave', reset)

    return () => {
      element.removeEventListener('pointerenter', handlePointerEnter)
      element.removeEventListener('pointermove', handlePointerMove)
      element.removeEventListener('pointerleave', reset)
      reset()
    }
  }, [ref, mode, finePointer, tiltEnabled])
}
