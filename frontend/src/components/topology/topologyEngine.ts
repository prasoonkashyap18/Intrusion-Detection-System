/**
 * Dependency-free 3D topology renderer on Canvas 2D.
 *
 * Framework-agnostic: React (NetworkTopology3D) owns input handling and calls
 * into this engine. Frames are rendered on demand — the animation loop runs
 * only while the camera, parallax or hover emphasis is still converging, then
 * stops. Suitable for the MVP's node counts; a WebGL renderer can replace it
 * behind the same component if graphs grow large.
 */

import type { TrafficLevel } from '../../types/security'
import type { TopologyGraph, TopologyNode } from '../../types/topology'
import { readThemeColor, rgba, type Rgb } from '../../utils/color'

export interface TopologyPalette {
  nodeLight: Rgb
  nodeMid: Rgb
  nodeDark: Rgb
  specular: Rgb
  edge: Rgb
  rim: Rgb
  shadow: Rgb
  floor: Rgb
  severity: Record<TrafficLevel, Rgb>
}

export interface ScreenAnchor {
  x: number
  y: number
  radius: number
}

interface CameraState {
  yaw: number
  pitch: number
  distance: number
  targetX: number
  targetY: number
  targetZ: number
}

interface View {
  cosYaw: number
  sinYaw: number
  cosPitch: number
  sinPitch: number
  focal: number
  centerX: number
  centerY: number
  camera: CameraState
}

interface ProjectedNode {
  node: TopologyNode
  x: number
  y: number
  depth: number
  radius: number
  /** 1 for the nearest node, fading toward the back for atmospheric depth. */
  fade: number
}

interface ResolvedEdge {
  source: TopologyNode
  target: TopologyNode
}

const CAMERA_KEYS = ['yaw', 'pitch', 'distance', 'targetX', 'targetY', 'targetZ'] as const
const DEFAULT_CAMERA: CameraState = { yaw: 0.62, pitch: 0.42, distance: 25.5, targetX: 0, targetY: 0.6, targetZ: 0 }
const FIELD_OF_VIEW = (32 * Math.PI) / 180
const PITCH_RANGE = [0.1, 1.15] as const
const DISTANCE_RANGE = [9, 34] as const
const FOCUS_DISTANCE = 15
const GROUND_Y = -1.6
const FLOOR_RINGS = [3.4, 6.9, 10.2]
const HOVER_SCALE = 1.22
const NEIGHBOR_SCALE = 1.07
const CAMERA_SMOOTHING = 0.14
const PARALLAX_SMOOTHING = 0.08
const SCALE_SMOOTHING = 0.22
const INERTIA_DECAY = 0.92
const PARALLAX_YAW = 0.07
const PARALLAX_PITCH = 0.04
const TAU = Math.PI * 2

function clamp(value: number, [min, max]: readonly [number, number]): number {
  return Math.min(max, Math.max(min, value))
}

export function readTopologyPalette(): TopologyPalette {
  return {
    nodeLight: readThemeColor('graphite-400'),
    nodeMid: readThemeColor('graphite-700'),
    nodeDark: readThemeColor('graphite-950'),
    specular: readThemeColor('white'),
    edge: readThemeColor('accent-500'),
    rim: readThemeColor('accent-400'),
    shadow: readThemeColor('graphite-950'),
    floor: readThemeColor('graphite-900'),
    severity: {
      normal: readThemeColor('sev-normal'),
      low: readThemeColor('sev-low'),
      medium: readThemeColor('sev-medium'),
      high: readThemeColor('sev-high'),
      critical: readThemeColor('sev-critical'),
    },
  }
}

export class TopologyEngine {
  /** Called after every rendered frame (e.g. to position DOM overlays). */
  onFrame: (() => void) | null = null

  private readonly canvas: HTMLCanvasElement
  private readonly context: CanvasRenderingContext2D
  private readonly palette: TopologyPalette
  private width = 0
  private height = 0
  private nodes: TopologyNode[] = []
  private nodeById = new Map<string, TopologyNode>()
  private edges: ResolvedEdge[] = []
  private neighbors = new Map<string, Set<string>>()
  private readonly camera: CameraState = { ...DEFAULT_CAMERA }
  private readonly goal: CameraState = { ...DEFAULT_CAMERA }
  private readonly parallax = { yaw: 0, pitch: 0 }
  private readonly parallaxGoal = { yaw: 0, pitch: 0 }
  private readonly velocity = { yaw: 0, pitch: 0 }
  private readonly scales = new Map<string, number>()
  private hoveredId: string | null = null
  private selectedId: string | null = null
  private projected: ProjectedNode[] = []
  private projectedById = new Map<string, ProjectedNode>()
  private frameId = 0
  private reducedMotion = false

  constructor(canvas: HTMLCanvasElement, palette: TopologyPalette) {
    const context = canvas.getContext('2d')
    if (!context) throw new Error('Canvas 2D rendering is not supported in this browser')
    this.canvas = canvas
    this.context = context
    this.palette = palette
  }

  setGraph(graph: TopologyGraph): void {
    this.nodes = graph.nodes
    this.nodeById = new Map(graph.nodes.map((node) => [node.id, node]))
    this.neighbors = new Map()
    this.edges = []
    for (const edge of graph.edges) {
      const source = this.nodeById.get(edge.source)
      const target = this.nodeById.get(edge.target)
      if (!source || !target) continue
      this.edges.push({ source, target })
      this.link(source.id, target.id)
      this.link(target.id, source.id)
    }
    this.draw()
  }

  /** Sizes the backing store for crisp output; draws synchronously to avoid a blank frame. */
  resize(width: number, height: number): void {
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2)
    this.width = width
    this.height = height
    this.canvas.width = Math.max(1, Math.round(width * pixelRatio))
    this.canvas.height = Math.max(1, Math.round(height * pixelRatio))
    this.context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0)
    this.draw()
  }

  setReducedMotion(reduced: boolean): void {
    this.reducedMotion = reduced
    if (reduced) {
      this.velocity.yaw = 0
      this.velocity.pitch = 0
    }
    this.requestFrame()
  }

  /** Normalized cursor position (-1…1) used for a subtle environmental response. */
  setParallax(x: number, y: number): void {
    this.parallaxGoal.yaw = x * PARALLAX_YAW
    this.parallaxGoal.pitch = y * PARALLAX_PITCH
    this.requestFrame()
  }

  rotateBy(yaw: number, pitch: number): void {
    this.velocity.yaw = 0
    this.velocity.pitch = 0
    this.goal.yaw += yaw
    this.goal.pitch = clamp(this.goal.pitch + pitch, PITCH_RANGE)
    this.requestFrame()
  }

  /** Continues a drag rotation with decaying momentum. */
  fling(yaw: number, pitch: number): void {
    if (this.reducedMotion) return
    this.velocity.yaw = yaw
    this.velocity.pitch = pitch
    this.requestFrame()
  }

  zoomBy(factor: number): void {
    this.goal.distance = clamp(this.goal.distance * factor, DISTANCE_RANGE)
    this.requestFrame()
  }

  setHovered(id: string | null): void {
    if (this.hoveredId === id) return
    this.hoveredId = id
    this.requestFrame()
  }

  /** Focuses the camera on a node, or returns to the overview when `id` is null. */
  select(id: string | null): void {
    const node = id ? this.nodeById.get(id) : undefined
    this.selectedId = node ? node.id : null
    if (node) {
      ;[this.goal.targetX, this.goal.targetY, this.goal.targetZ] = node.position
      this.goal.distance = Math.min(this.goal.distance, FOCUS_DISTANCE)
    } else {
      this.goal.targetX = DEFAULT_CAMERA.targetX
      this.goal.targetY = DEFAULT_CAMERA.targetY
      this.goal.targetZ = DEFAULT_CAMERA.targetZ
    }
    this.requestFrame()
  }

  resetView(): void {
    Object.assign(this.goal, DEFAULT_CAMERA)
    this.selectedId = null
    this.velocity.yaw = 0
    this.velocity.pitch = 0
    this.requestFrame()
  }

  /** Returns the front-most node under a canvas-space point. */
  pick(x: number, y: number): TopologyNode | null {
    for (let index = this.projected.length - 1; index >= 0; index -= 1) {
      const item = this.projected[index]
      if (!item) continue
      const reach = item.radius + 4
      if ((x - item.x) ** 2 + (y - item.y) ** 2 <= reach * reach) return item.node
    }
    return null
  }

  getHoveredAnchor(): ScreenAnchor | null {
    const item = this.hoveredId ? this.projectedById.get(this.hoveredId) : undefined
    return item ? { x: item.x, y: item.y, radius: item.radius } : null
  }

  destroy(): void {
    if (this.frameId) window.cancelAnimationFrame(this.frameId)
    this.frameId = 0
    this.onFrame = null
  }

  private link(from: string, to: string): void {
    const set = this.neighbors.get(from) ?? new Set<string>()
    set.add(to)
    this.neighbors.set(from, set)
  }

  private requestFrame(): void {
    if (!this.frameId) this.frameId = window.requestAnimationFrame(this.tick)
  }

  private readonly tick = (): void => {
    this.frameId = 0
    const animating = this.advance()
    this.draw()
    if (animating) this.requestFrame()
  }

  /** Moves camera, parallax and hover emphasis toward their goals; returns true while still moving. */
  private advance(): boolean {
    let animating = false
    const { velocity, goal, camera } = this

    if (velocity.yaw !== 0 || velocity.pitch !== 0) {
      goal.yaw += velocity.yaw
      goal.pitch = clamp(goal.pitch + velocity.pitch, PITCH_RANGE)
      velocity.yaw *= INERTIA_DECAY
      velocity.pitch *= INERTIA_DECAY
      if (Math.abs(velocity.yaw) + Math.abs(velocity.pitch) < 1e-4) {
        velocity.yaw = 0
        velocity.pitch = 0
      } else {
        animating = true
      }
    }

    const cameraSmoothing = this.reducedMotion ? 1 : CAMERA_SMOOTHING
    for (const key of CAMERA_KEYS) {
      const delta = goal[key] - camera[key]
      if (Math.abs(delta) > 1e-4) {
        camera[key] += delta * cameraSmoothing
        animating = true
      } else {
        camera[key] = goal[key]
      }
    }

    const parallaxSmoothing = this.reducedMotion ? 1 : PARALLAX_SMOOTHING
    for (const axis of ['yaw', 'pitch'] as const) {
      const delta = this.parallaxGoal[axis] - this.parallax[axis]
      if (Math.abs(delta) > 1e-4) {
        this.parallax[axis] += delta * parallaxSmoothing
        animating = true
      } else {
        this.parallax[axis] = this.parallaxGoal[axis]
      }
    }

    const hoveredNeighbors = this.hoveredId ? this.neighbors.get(this.hoveredId) : undefined
    const scaleSmoothing = this.reducedMotion ? 1 : SCALE_SMOOTHING
    for (const node of this.nodes) {
      const emphasized = node.id === this.hoveredId || node.id === this.selectedId
      const target = emphasized ? HOVER_SCALE : hoveredNeighbors?.has(node.id) ? NEIGHBOR_SCALE : 1
      const current = this.scales.get(node.id) ?? 1
      const delta = target - current
      if (Math.abs(delta) > 1e-3) {
        this.scales.set(node.id, current + delta * scaleSmoothing)
        animating = true
      } else {
        this.scales.set(node.id, target)
      }
    }

    return animating
  }

  private draw(): void {
    const { context, width, height } = this
    context.clearRect(0, 0, width, height)
    if (width > 0 && height > 0) {
      const view = this.createView()
      this.drawFloor(view)
      this.projectNodes(view)
      this.drawShadows(view)
      this.drawEdges()
      this.drawNodes()
    }
    this.onFrame?.()
  }

  private createView(): View {
    const yaw = this.camera.yaw + this.parallax.yaw
    const pitch = this.camera.pitch + this.parallax.pitch
    return {
      cosYaw: Math.cos(yaw),
      sinYaw: Math.sin(yaw),
      cosPitch: Math.cos(pitch),
      sinPitch: Math.sin(pitch),
      // Fit by the narrower dimension so the scene stays in frame on narrow screens.
      focal: Math.min(this.height, this.width * 0.8) / 2 / Math.tan(FIELD_OF_VIEW / 2),
      centerX: this.width / 2,
      centerY: this.height * 0.46,
      camera: this.camera,
    }
  }

  private project(view: View, x: number, y: number, z: number): { x: number; y: number; depth: number } | null {
    const { camera } = view
    const dx = x - camera.targetX
    const dy = y - camera.targetY
    const dz = z - camera.targetZ
    const rotatedX = dx * view.cosYaw - dz * view.sinYaw
    const rotatedZ = dx * view.sinYaw + dz * view.cosYaw
    // Positive pitch looks down onto the scene: far points rise, high points come closer.
    const viewY = dy * view.cosPitch + rotatedZ * view.sinPitch
    const viewZ = -dy * view.sinPitch + rotatedZ * view.cosPitch
    const depth = viewZ + camera.distance
    if (depth < 0.5) return null
    return {
      x: view.centerX + (rotatedX * view.focal) / depth,
      y: view.centerY - (viewY * view.focal) / depth,
      depth,
    }
  }

  private drawFloor(view: View): void {
    const { context, palette } = this
    context.lineWidth = 1
    FLOOR_RINGS.forEach((radius, index) => {
      context.beginPath()
      let drawing = false
      for (let step = 0; step <= 120; step += 1) {
        const angle = (step / 120) * TAU
        const point = this.project(view, Math.cos(angle) * radius, GROUND_Y, Math.sin(angle) * radius)
        if (!point) {
          drawing = false
          continue
        }
        if (drawing) context.lineTo(point.x, point.y)
        else context.moveTo(point.x, point.y)
        drawing = true
      }
      const outermost = index === FLOOR_RINGS.length - 1
      context.setLineDash(outermost ? [2, 6] : [])
      context.strokeStyle = rgba(palette.floor, outermost ? 0.08 : 0.07)
      context.stroke()
    })
    context.setLineDash([])

    const reach = FLOOR_RINGS[FLOOR_RINGS.length - 1] ?? 10
    for (const [axisX, axisZ] of [
      [1, 0],
      [0, 1],
    ] as const) {
      const start = this.project(view, -reach * axisX, GROUND_Y, -reach * axisZ)
      const end = this.project(view, reach * axisX, GROUND_Y, reach * axisZ)
      if (!start || !end) continue
      context.strokeStyle = rgba(palette.floor, 0.045)
      context.beginPath()
      context.moveTo(start.x, start.y)
      context.lineTo(end.x, end.y)
      context.stroke()
    }
  }

  private projectNodes(view: View): void {
    const projected: ProjectedNode[] = []
    for (const node of this.nodes) {
      const [x, y, z] = node.position
      const point = this.project(view, x, y, z)
      if (!point) continue
      const scale = this.scales.get(node.id) ?? 1
      projected.push({
        node,
        x: point.x,
        y: point.y,
        depth: point.depth,
        radius: (node.size * view.focal * scale) / point.depth,
        fade: 1,
      })
    }
    // Back to front, so nearer nodes paint over farther ones.
    projected.sort((a, b) => b.depth - a.depth)
    const nearest = projected.at(-1)?.depth ?? 0
    const farthest = projected[0]?.depth ?? 0
    const range = Math.max(farthest - nearest, 1e-3)
    for (const item of projected) item.fade = 1 - ((item.depth - nearest) / range) * 0.5
    this.projected = projected
    this.projectedById = new Map(projected.map((item) => [item.node.id, item]))
  }

  private drawShadows(view: View): void {
    const { context, palette } = this
    const flatten = Math.max(0.14, Math.sin(this.camera.pitch + this.parallax.pitch))
    for (const item of this.projected) {
      const [x, y, z] = item.node.position
      const ground = this.project(view, x, GROUND_Y, z)
      if (!ground) continue
      const elevation = Math.max(0, y - GROUND_Y)

      // Hairline stem anchoring the node to its shadow for depth perception.
      context.strokeStyle = rgba(palette.floor, 0.07 * item.fade)
      context.lineWidth = 1
      context.beginPath()
      context.moveTo(item.x, item.y)
      context.lineTo(ground.x, ground.y)
      context.stroke()

      const radius = ((item.node.size * view.focal) / ground.depth) * (1.5 + elevation * 0.22)
      const alpha = (0.22 / (1 + elevation * 0.45)) * item.fade
      context.save()
      context.translate(ground.x, ground.y)
      context.scale(1, flatten)
      const gradient = context.createRadialGradient(0, 0, 0, 0, 0, radius)
      gradient.addColorStop(0, rgba(palette.shadow, alpha))
      gradient.addColorStop(1, rgba(palette.shadow, 0))
      context.fillStyle = gradient
      context.beginPath()
      context.arc(0, 0, radius, 0, TAU)
      context.fill()
      context.restore()
    }
  }

  private drawEdges(): void {
    const { context, palette } = this
    const focusId = this.hoveredId ?? this.selectedId
    context.lineCap = 'round'
    for (const edge of this.edges) {
      const a = this.projectedById.get(edge.source.id)
      const b = this.projectedById.get(edge.target.id)
      if (!a || !b) continue
      const related = focusId !== null && (edge.source.id === focusId || edge.target.id === focusId)
      const fade = (a.fade + b.fade) / 2
      const alpha = related ? 0.95 : (focusId === null ? 0.55 : 0.22) * fade
      context.strokeStyle = rgba(palette.edge, alpha)
      context.lineWidth = related ? 1.5 : 1
      context.beginPath()
      context.moveTo(a.x, a.y)
      context.lineTo(b.x, b.y)
      context.stroke()
    }
  }

  private drawNodes(): void {
    const { context, palette } = this
    for (const { node, x, y, radius, fade } of this.projected) {
      if (radius < 0.5) continue
      const hovered = node.id === this.hoveredId
      const selected = node.id === this.selectedId

      // Graphite material: lit from the upper left.
      const material = context.createRadialGradient(x - radius * 0.38, y - radius * 0.42, radius * 0.08, x, y, radius)
      material.addColorStop(0, rgba(palette.nodeLight, fade))
      material.addColorStop(0.55, rgba(palette.nodeMid, fade))
      material.addColorStop(1, rgba(palette.nodeDark, fade))
      context.fillStyle = material
      context.beginPath()
      context.arc(x, y, radius, 0, TAU)
      context.fill()

      // Restrained cyan edge light along the lower rim; a full ring when emphasized.
      const emphasized = hovered || selected
      context.lineWidth = emphasized ? 1.4 : 1
      context.strokeStyle = rgba(palette.rim, emphasized ? 0.9 : 0.32 * fade)
      context.beginPath()
      if (emphasized) context.arc(x, y, Math.max(radius - 0.6, 0.5), 0, TAU)
      else context.arc(x, y, Math.max(radius - 0.6, 0.5), 0.12 * Math.PI, 0.88 * Math.PI)
      context.stroke()

      context.fillStyle = rgba(palette.specular, 0.34 * fade)
      context.beginPath()
      context.ellipse(x - radius * 0.34, y - radius * 0.4, radius * 0.24, radius * 0.15, -0.5, 0, TAU)
      context.fill()

      // Security state appears only when the node carries a real detection level.
      if (node.level && node.level !== 'normal') {
        context.lineWidth = node.level === 'critical' ? 2 : 1.5
        context.strokeStyle = rgba(palette.severity[node.level], 0.9)
        context.beginPath()
        context.arc(x, y, radius + 3.5, 0, TAU)
        context.stroke()
      }

      if (selected) this.drawReticle(x, y, radius + 7)
      else if (hovered) {
        context.lineWidth = 1
        context.strokeStyle = rgba(palette.rim, 0.4)
        context.beginPath()
        context.arc(x, y, radius + 5, 0, TAU)
        context.stroke()
      }
    }
  }

  /** Precise focus marker: a thin ring with four ticks. */
  private drawReticle(x: number, y: number, radius: number): void {
    const { context, palette } = this
    context.lineWidth = 1
    context.strokeStyle = rgba(palette.rim, 0.85)
    context.beginPath()
    context.arc(x, y, radius, 0, TAU)
    context.stroke()
    for (let tick = 0; tick < 4; tick += 1) {
      const angle = (tick / 4) * TAU + Math.PI / 4
      context.beginPath()
      context.moveTo(x + Math.cos(angle) * (radius + 2), y + Math.sin(angle) * (radius + 2))
      context.lineTo(x + Math.cos(angle) * (radius + 6), y + Math.sin(angle) * (radius + 6))
      context.stroke()
    }
  }
}
