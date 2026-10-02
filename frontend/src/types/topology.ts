import type { TrafficLevel } from './security'

export interface TopologyNode {
  id: string
  /** World-space position; y is up. */
  position: readonly [number, number, number]
  /** World-space radius. */
  size: number
  /** Security state. Set only from real detection results, never for decoration. */
  level?: TrafficLevel
  label?: string
}

export interface TopologyEdge {
  id: string
  source: string
  target: string
}

export interface TopologyGraph {
  nodes: TopologyNode[]
  edges: TopologyEdge[]
}
