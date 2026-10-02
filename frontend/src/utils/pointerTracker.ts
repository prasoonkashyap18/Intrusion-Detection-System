/**
 * Shared, framework-agnostic pointer tracker.
 *
 * One passive `pointermove` listener serves every subscriber, and updates are
 * coalesced to at most one notification per animation frame. Subscribers are
 * expected to write to the DOM directly (CSS custom properties / transforms)
 * rather than setting React state, so pointer movement never re-renders.
 */

export interface PointerSnapshot {
  /** Viewport coordinates in CSS pixels. */
  x: number
  y: number
  /** Position normalized to the viewport, from -1 (left/top) to 1 (right/bottom). */
  nx: number
  ny: number
  /** False once the pointer leaves the document or the window loses focus. */
  inside: boolean
}

type PointerListener = (snapshot: PointerSnapshot) => void

const listeners = new Set<PointerListener>()
let snapshot: PointerSnapshot = { x: 0, y: 0, nx: 0, ny: 0, inside: false }
let frameId = 0

function notify() {
  frameId = 0
  for (const listener of listeners) listener(snapshot)
}

function scheduleNotify() {
  if (!frameId) frameId = window.requestAnimationFrame(notify)
}

function handlePointerMove(event: PointerEvent) {
  if (event.pointerType === 'touch') return
  const width = window.innerWidth || 1
  const height = window.innerHeight || 1
  snapshot = {
    x: event.clientX,
    y: event.clientY,
    nx: (event.clientX / width) * 2 - 1,
    ny: (event.clientY / height) * 2 - 1,
    inside: true,
  }
  scheduleNotify()
}

function handlePointerExit() {
  snapshot = { ...snapshot, inside: false }
  scheduleNotify()
}

function start() {
  window.addEventListener('pointermove', handlePointerMove, { passive: true })
  document.documentElement.addEventListener('pointerleave', handlePointerExit)
  window.addEventListener('blur', handlePointerExit)
}

function stop() {
  window.removeEventListener('pointermove', handlePointerMove)
  document.documentElement.removeEventListener('pointerleave', handlePointerExit)
  window.removeEventListener('blur', handlePointerExit)
  if (frameId) window.cancelAnimationFrame(frameId)
  frameId = 0
}

/** Subscribes to pointer updates; listening starts with the first subscriber and stops with the last. */
export function subscribeToPointer(listener: PointerListener): () => void {
  if (listeners.size === 0) start()
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) stop()
  }
}
