import { cn } from '../../utils/cn'
import { Panel } from '../ui/Panel'
import { StatusDot } from '../ui/StatusDot'
import { NetworkTopology3D, type NodeDescription } from './NetworkTopology3D'
import { createPreviewTopology } from './previewTopology'

const PREVIEW_TOPOLOGY = createPreviewTopology()

const describePreviewNode = (): NodeDescription => ({
  title: 'Preview node',
  detail: 'Illustrative geometry — no host data',
})

export function TopologyViewport({ className }: { className?: string }) {
  return (
    <Panel interaction="spotlight" aria-labelledby="topology-heading" className={cn('overflow-hidden', className)}>
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3 px-6 pt-6 lg:px-8 lg:pt-7">
        <div className="max-w-xl">
          <h2 id="topology-heading" className="text-lg font-semibold tracking-tight text-graphite-950">
            Network topology
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-graphite-500">
            Hosts and flows will be rendered from processed detection results. Until then this view shows
            illustrative preview geometry — it does not represent any network.
          </p>
        </div>
        <span className="inline-flex items-center gap-2 rounded-full bg-ice-50 px-3 py-1 text-xs font-medium text-graphite-700 ring-1 ring-ice-200">
          <StatusDot tone="ice" />
          Preview geometry · not live data
        </span>
      </div>

      <div className="topology-atmosphere relative mt-5 h-[360px] border-t border-graphite-900/5 md:h-[440px] lg:h-[500px]">
        <NetworkTopology3D
          graph={PREVIEW_TOPOLOGY}
          label="Network topology preview showing illustrative geometry, not network data."
          describeNode={describePreviewNode}
          className="size-full"
        />

        <p className="pointer-events-none absolute top-4 left-5 hidden text-xs text-graphite-500 md:block lg:left-7">
          Drag to rotate · Ctrl/⌘ + scroll to zoom · Click a node to focus
        </p>

        <ul
          aria-label="Topology legend"
          className="pointer-events-none absolute bottom-4 left-5 hidden max-w-[calc(100%-16rem)] flex-wrap items-center gap-x-5 gap-y-2 rounded-xl bg-white/80 px-3.5 py-2 text-xs text-graphite-600 shadow-control ring-1 ring-graphite-900/6 backdrop-blur-sm sm:flex lg:left-7"
        >
          <li className="flex items-center gap-2">
            <span aria-hidden="true" className="size-2.5 rounded-full bg-radial-[at_35%_30%] from-graphite-400 to-graphite-950" />
            Host
          </li>
          <li className="flex items-center gap-2">
            <span aria-hidden="true" className="h-px w-4 bg-accent-500" />
            Connection
          </li>
          <li className="flex items-center gap-2">
            <span aria-hidden="true" className="flex gap-0.5">
              <span className="size-1.5 rounded-full bg-sev-low" />
              <span className="size-1.5 rounded-full bg-sev-medium" />
              <span className="size-1.5 rounded-full bg-sev-high" />
              <span className="size-1.5 rounded-full bg-sev-critical" />
            </span>
            Severity tint · real detections only
          </li>
        </ul>
      </div>
    </Panel>
  )
}
