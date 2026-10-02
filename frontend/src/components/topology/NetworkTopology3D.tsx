import { LocateFixed, Minus, Plus, RotateCcw, RotateCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useFinePointer, usePrefersReducedMotion } from '../../hooks/useMediaQuery'
import type { TopologyGraph, TopologyNode } from '../../types/topology'
import { cn } from '../../utils/cn'
import { subscribeToPointer } from '../../utils/pointerTracker'
import { Button } from '../ui/Button'
import { readTopologyPalette, TopologyEngine } from './topologyEngine'

export interface NodeDescription {
  title: string
  detail?: string
}

interface NetworkTopology3DProps {
  graph: TopologyGraph
  /** Accessible description of what the scene currently shows. */
  label: string
  describeNode: (node: TopologyNode) => NodeDescription
  /** Double-click on a node, e.g. to open entity details once real host data exists. */
  onNodeOpen?: (node: TopologyNode) => void
  className?: string
}

const DRAG_THRESHOLD_PX = 4
const YAW_PER_PX = 0.0055
const PITCH_PER_PX = 0.0042
const ROTATE_STEP = 0.35
const ZOOM_STEP = 1.18
const WHEEL_ZOOM_RATE = 0.0015
const FLING_WINDOW_MS = 80

export function NetworkTopology3D({ graph, label, describeNode, onNodeOpen, className }: NetworkTopology3DProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const engineRef = useRef<TopologyEngine | null>(null)
  const onNodeOpenRef = useRef(onNodeOpen)
  const [hoveredNode, setHoveredNode] = useState<TopologyNode | null>(null)
  const finePointer = useFinePointer()
  const reducedMotion = usePrefersReducedMotion()

  useEffect(() => {
    onNodeOpenRef.current = onNodeOpen
  }, [onNodeOpen])

  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return undefined

    const engine = new TopologyEngine(canvas, readTopologyPalette())
    engineRef.current = engine
    engine.onFrame = () => {
      const tooltip = tooltipRef.current
      const anchor = engine.getHoveredAnchor()
      if (tooltip && anchor) {
        tooltip.style.transform = `translate3d(${anchor.x}px, ${anchor.y - anchor.radius - 12}px, 0) translate(-50%, -100%)`
      }
    }

    const resizeObserver = new ResizeObserver(([entry]) => {
      if (entry) engine.resize(entry.contentRect.width, entry.contentRect.height)
    })
    resizeObserver.observe(container)

    return () => {
      resizeObserver.disconnect()
      engine.destroy()
      engineRef.current = null
    }
  }, [])

  useEffect(() => {
    engineRef.current?.setGraph(graph)
  }, [graph])

  useEffect(() => {
    engineRef.current?.setReducedMotion(reducedMotion)
  }, [reducedMotion])

  // Subtle response to the cursor anywhere on the page, only while the view is on screen.
  useEffect(() => {
    const engine = engineRef.current
    const container = containerRef.current
    if (!engine || !container || !finePointer || reducedMotion) return undefined

    let unsubscribe: (() => void) | null = null
    const observer = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) {
        unsubscribe ??= subscribeToPointer(({ nx, ny, inside }) => engine.setParallax(inside ? nx : 0, inside ? ny : 0))
      } else {
        unsubscribe?.()
        unsubscribe = null
        engine.setParallax(0, 0)
      }
    })
    observer.observe(container)

    return () => {
      observer.disconnect()
      unsubscribe?.()
      engine.setParallax(0, 0)
    }
  }, [finePointer, reducedMotion])

  useEffect(() => {
    const canvas = canvasRef.current
    const engine = engineRef.current
    if (!canvas || !engine) return undefined

    let activePointer: number | null = null
    let dragging = false
    // Plain-wheel zoom only after the user has pressed inside the view, so page scrolling is never hijacked.
    let engaged = false
    let startX = 0
    let startY = 0
    let lastX = 0
    let lastY = 0
    let lastTime = 0
    let flingYaw = 0
    let flingPitch = 0

    const toLocal = (event: MouseEvent) => {
      const bounds = canvas.getBoundingClientRect()
      return { x: event.clientX - bounds.left, y: event.clientY - bounds.top }
    }

    const updateHover = (node: TopologyNode | null) => {
      engine.setHovered(node?.id ?? null)
      setHoveredNode((current) => (current?.id === node?.id ? current : node))
    }

    const handlePointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return
      activePointer = event.pointerId
      dragging = false
      engaged = true
      startX = lastX = event.clientX
      startY = lastY = event.clientY
      lastTime = event.timeStamp
      flingYaw = 0
      flingPitch = 0
      try {
        canvas.setPointerCapture(event.pointerId)
      } catch {
        // Synthetic or already-released pointers cannot be captured; dragging still works without capture.
      }
    }

    const handlePointerMove = (event: PointerEvent) => {
      if (activePointer !== event.pointerId) {
        if (event.pointerType !== 'touch') {
          const { x, y } = toLocal(event)
          updateHover(engine.pick(x, y))
        }
        return
      }
      if (!dragging && Math.hypot(event.clientX - startX, event.clientY - startY) > DRAG_THRESHOLD_PX) {
        dragging = true
        canvas.dataset.dragging = ''
        updateHover(null)
      }
      if (!dragging) return
      const yaw = (event.clientX - lastX) * YAW_PER_PX
      // Touch drags rotate horizontally only; vertical swipes stay with page scrolling.
      const pitch = event.pointerType === 'touch' ? 0 : (event.clientY - lastY) * PITCH_PER_PX
      engine.rotateBy(yaw, pitch)
      const elapsed = Math.max(event.timeStamp - lastTime, 1)
      flingYaw = (yaw / elapsed) * 16
      flingPitch = (pitch / elapsed) * 16
      lastX = event.clientX
      lastY = event.clientY
      lastTime = event.timeStamp
    }

    const finishPointer = (event: PointerEvent, cancelled: boolean) => {
      if (activePointer !== event.pointerId) return
      activePointer = null
      if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId)
      if (dragging) {
        if (!cancelled && event.timeStamp - lastTime < FLING_WINDOW_MS) engine.fling(flingYaw, flingPitch)
      } else if (!cancelled) {
        const { x, y } = toLocal(event)
        engine.select(engine.pick(x, y)?.id ?? null)
      }
      dragging = false
      delete canvas.dataset.dragging
    }

    const handlePointerUp = (event: PointerEvent) => finishPointer(event, false)
    const handlePointerCancel = (event: PointerEvent) => finishPointer(event, true)

    const handlePointerLeave = () => {
      engaged = false
      if (activePointer === null) updateHover(null)
    }

    const handleDoubleClick = (event: MouseEvent) => {
      const { x, y } = toLocal(event)
      const node = engine.pick(x, y)
      if (node) onNodeOpenRef.current?.(node)
    }

    const handleWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey && !engaged) return
      event.preventDefault()
      const delta = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? event.deltaY * 16 : event.deltaY
      engine.zoomBy(Math.exp(delta * WHEEL_ZOOM_RATE))
    }

    canvas.addEventListener('pointerdown', handlePointerDown)
    canvas.addEventListener('pointermove', handlePointerMove)
    canvas.addEventListener('pointerup', handlePointerUp)
    canvas.addEventListener('pointercancel', handlePointerCancel)
    canvas.addEventListener('pointerleave', handlePointerLeave)
    canvas.addEventListener('dblclick', handleDoubleClick)
    canvas.addEventListener('wheel', handleWheel, { passive: false })

    return () => {
      canvas.removeEventListener('pointerdown', handlePointerDown)
      canvas.removeEventListener('pointermove', handlePointerMove)
      canvas.removeEventListener('pointerup', handlePointerUp)
      canvas.removeEventListener('pointercancel', handlePointerCancel)
      canvas.removeEventListener('pointerleave', handlePointerLeave)
      canvas.removeEventListener('dblclick', handleDoubleClick)
      canvas.removeEventListener('wheel', handleWheel)
    }
  }, [])

  const description = hoveredNode ? describeNode(hoveredNode) : null

  return (
    <div ref={containerRef} className={cn('relative overflow-hidden', className)}>
      <p className="sr-only">{label}</p>
      {/* Pointer-driven visualization; every camera action is also available via the toolbar below. */}
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        className={cn(
          'absolute inset-0 size-full touch-pan-y data-dragging:cursor-grabbing',
          hoveredNode ? 'cursor-pointer' : 'cursor-grab',
        )}
      />

      <div
        ref={tooltipRef}
        aria-hidden="true"
        className={cn(
          'pointer-events-none absolute top-0 left-0 z-10 max-w-56 rounded-lg bg-white/95 px-3 py-2 shadow-raised ring-1 ring-graphite-900/8 backdrop-blur-sm transition-opacity duration-150',
          description ? 'opacity-100' : 'opacity-0',
        )}
      >
        {description && (
          <>
            <p className="text-xs font-medium text-graphite-950">{description.title}</p>
            {description.detail && <p className="mt-0.5 text-[11px] leading-snug text-graphite-500">{description.detail}</p>}
          </>
        )}
      </div>

      <div
        role="toolbar"
        aria-label="Topology view controls"
        className="absolute right-4 bottom-4 flex items-center gap-0.5 rounded-xl bg-white/85 p-1 shadow-control ring-1 ring-graphite-900/7 backdrop-blur-sm"
      >
        <Button variant="ghost" size="icon" magnetic aria-label="Rotate left" onClick={() => engineRef.current?.rotateBy(-ROTATE_STEP, 0)}>
          <RotateCcw className="size-4" aria-hidden="true" />
        </Button>
        <Button variant="ghost" size="icon" magnetic aria-label="Rotate right" onClick={() => engineRef.current?.rotateBy(ROTATE_STEP, 0)}>
          <RotateCw className="size-4" aria-hidden="true" />
        </Button>
        <span aria-hidden="true" className="mx-0.5 h-4 w-px bg-graphite-900/10" />
        <Button variant="ghost" size="icon" magnetic aria-label="Zoom in" onClick={() => engineRef.current?.zoomBy(1 / ZOOM_STEP)}>
          <Plus className="size-4" aria-hidden="true" />
        </Button>
        <Button variant="ghost" size="icon" magnetic aria-label="Zoom out" onClick={() => engineRef.current?.zoomBy(ZOOM_STEP)}>
          <Minus className="size-4" aria-hidden="true" />
        </Button>
        <span aria-hidden="true" className="mx-0.5 h-4 w-px bg-graphite-900/10" />
        <Button variant="ghost" size="icon" magnetic aria-label="Reset view" onClick={() => engineRef.current?.resetView()}>
          <LocateFixed className="size-4" aria-hidden="true" />
        </Button>
      </div>
    </div>
  )
}
