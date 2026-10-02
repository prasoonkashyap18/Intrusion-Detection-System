import type { TopologyEdge, TopologyGraph, TopologyNode } from '../../types/topology'

const TAU = Math.PI * 2

/**
 * Deterministic, illustrative geometry used to preview the topology view
 * before any network data exists. It does not represent hosts, flows or
 * security state: nodes carry no labels and no severity level.
 */
export function createPreviewTopology(): TopologyGraph {
  const nodes: TopologyNode[] = [{ id: 'core', position: [0, 1, 0], size: 0.5 }]
  const edges: TopologyEdge[] = []
  const connect = (source: string, target: string) => edges.push({ id: `${source}~${target}`, source, target })

  const innerCount = 6
  for (let index = 0; index < innerCount; index += 1) {
    const angle = (index / innerCount) * TAU + 0.26
    const id = `inner-${index}`
    nodes.push({ id, position: [Math.cos(angle) * 3.4, 0.3 + (index % 2) * 0.55, Math.sin(angle) * 3.4], size: 0.34 })
    connect('core', id)
    connect(id, `inner-${(index + 1) % innerCount}`)
  }

  const outerCount = 12
  for (let index = 0; index < outerCount; index += 1) {
    const angle = (index / outerCount) * TAU
    const radius = 6.6 + (index % 3) * 0.4
    const id = `outer-${index}`
    nodes.push({
      id,
      position: [Math.cos(angle) * radius, -0.25 + 0.6 * Math.sin(index * 1.7), Math.sin(angle) * radius],
      size: 0.24,
    })
    connect(`inner-${Math.floor(index / 2)}`, id)
    if (index % 3 === 0) connect(id, `outer-${(index + 1) % outerCount}`)
  }

  const upperCount = 3
  for (let index = 0; index < upperCount; index += 1) {
    const angle = (index / upperCount) * TAU + 0.6
    const id = `upper-${index}`
    nodes.push({ id, position: [Math.cos(angle) * 2.1, 2.8, Math.sin(angle) * 2.1], size: 0.28 })
    connect('core', id)
    connect(id, `inner-${(index * 2) % innerCount}`)
  }

  return { nodes, edges }
}
