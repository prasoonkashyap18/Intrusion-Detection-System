import { useEffect, type RefObject } from 'react'
import { useFinePointer, usePrefersReducedMotion } from './useMediaQuery'

const MAX_OFFSET_PX = 3

/**
 * Magnetic hover: the element drifts a few pixels toward the cursor while
 * hovered, via --magnet-x / --magnet-y consumed by the `.magnetic` class.
 * No React re-renders; disabled for touch input and reduced motion.
 */
export function useMagnetic(ref: RefObject<HTMLElement | null>, enabled: boolean): void {
  const finePointer = useFinePointer()
  const reducedMotion = usePrefersReducedMotion()
  const active = enabled && finePointer && !reducedMotion

  useEffect(() => {
    const element = ref.current
    if (!active || !element) return undefined

    let bounds: DOMRect | null = null
    let frameId = 0
    let pointerX = 0
    let pointerY = 0

    const render = () => {
      frameId = 0
      if (!bounds) return
      const offsetX = (pointerX - (bounds.left + bounds.width / 2)) / (bounds.width / 2)
      const offsetY = (pointerY - (bounds.top + bounds.height / 2)) / (bounds.height / 2)
      element.style.setProperty('--magnet-x', `${(offsetX * MAX_OFFSET_PX).toFixed(2)}px`)
      element.style.setProperty('--magnet-y', `${(offsetY * MAX_OFFSET_PX).toFixed(2)}px`)
    }

    const handlePointerEnter = (event: PointerEvent) => {
      if (event.pointerType === 'touch') return
      bounds = element.getBoundingClientRect()
      element.dataset.magnetActive = ''
    }

    const handlePointerMove = (event: PointerEvent) => {
      if (!bounds || event.pointerType === 'touch') return
      pointerX = event.clientX
      pointerY = event.clientY
      if (!frameId) frameId = window.requestAnimationFrame(render)
    }

    const reset = () => {
      if (frameId) window.cancelAnimationFrame(frameId)
      frameId = 0
      bounds = null
      delete element.dataset.magnetActive
      element.style.removeProperty('--magnet-x')
      element.style.removeProperty('--magnet-y')
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
  }, [ref, active])
}
