import { useRef, type ReactNode } from 'react'
import { usePointerParallax } from '../../hooks/usePointerParallax'
import { cn } from '../../utils/cn'
import { NoData } from '../ui/NoData'
import { Panel } from '../ui/Panel'
import { SectionHeading } from '../ui/SectionHeading'
import { StatusDot } from '../ui/StatusDot'
import './topology.css'

interface TopologyViewportProps {
  /**
   * Scene content rendered inside the viewport frame (e.g. a lazily loaded
   * WebGL topology). When omitted, the standby stage and empty state render.
   */
  children?: ReactNode
  className?: string
}

export function TopologyViewport({ children, className }: TopologyViewportProps) {
  return (
    <Panel
      interaction="spotlight"
      aria-labelledby="topology-heading"
      className={cn('flex min-h-[380px] flex-col overflow-hidden lg:min-h-[460px]', className)}
    >
      <SectionHeading
        id="topology-heading"
        eyebrow="Network topology"
        title="Topology view"
        className="relative z-10 p-5"
        action={
          <span className="inline-flex items-center gap-2 rounded-full border border-white/8 bg-obsidian-900/60 px-2.5 py-1 font-mono text-micro uppercase tracking-label text-ink-400">
            <StatusDot tone="muted" />
            Standby
          </span>
        }
      />
      <div className="relative flex-1">{children ?? <StandbyStage />}</div>
    </Panel>
  )
}

function StandbyStage() {
  const sceneRef = useRef<HTMLDivElement>(null)
  usePointerParallax(sceneRef)

  return (
    <>
      <div aria-hidden="true" className="topology-stage">
        <div ref={sceneRef} className="topology-scene">
          <div className="topology-grid" />
          <div className="topology-ring topology-ring-outer" />
          <div className="topology-ring topology-ring-mid" />
          <div className="topology-ring topology-ring-inner" />
          <div className="topology-core" />
          <div className="topology-beam" />
        </div>
      </div>

      <HudCorners />

      <div className="relative z-10 flex h-full flex-col justify-end p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="max-w-sm">
            <p className="text-sm font-medium text-ink-100">No network-flow data ingested</p>
            <p className="mt-1.5 text-xs leading-relaxed text-ink-400">
              The interactive 3D topology will be generated from processed detection results. Nothing is
              rendered from simulated traffic.
            </p>
          </div>
          <dl className="flex gap-5 font-mono text-micro uppercase tracking-label">
            <div className="flex gap-2">
              <dt className="text-ink-500">Hosts</dt>
              <dd className="text-ink-300">
                <NoData />
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-ink-500">Flows</dt>
              <dd className="text-ink-300">
                <NoData />
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </>
  )
}

function HudCorners() {
  const corner = 'absolute size-3.5 border-ice/30'
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-3">
      <span className={cn(corner, 'top-0 left-0 border-t border-l')} />
      <span className={cn(corner, 'top-0 right-0 border-t border-r')} />
      <span className={cn(corner, 'bottom-0 left-0 border-b border-l')} />
      <span className={cn(corner, 'right-0 bottom-0 border-r border-b')} />
    </div>
  )
}
