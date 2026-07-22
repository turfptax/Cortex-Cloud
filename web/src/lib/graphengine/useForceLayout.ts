import { useEffect, useRef, useState } from 'react'
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
} from 'd3-force'
import type {
  EngineEdge,
  EngineNode,
  ForceLayoutConfig,
  PositionedEngineNode,
} from './types'

export interface UseForceLayoutArgs<TNodeData = unknown> {
  nodes: EngineNode<TNodeData>[] | null
  edges: EngineEdge[] | null
  width: number
  height: number
  config?: ForceLayoutConfig
  nodeRadius?: (node: EngineNode<TNodeData>) => number
}

const DEFAULTS = {
  alphaDecay: 0.012,
  alpha: 0.6,
  linkDistance: 110,
  linkStrength: 0.4,
  charge: -280,
  collidePadding: 8,
} as const

export function useForceLayout<TNodeData = unknown>({
  nodes,
  edges,
  width,
  height,
  config,
  nodeRadius,
}: UseForceLayoutArgs<TNodeData>): PositionedEngineNode<TNodeData>[] {
  const [positioned, setPositioned] = useState<PositionedEngineNode<TNodeData>[]>([])
  const prevPosRef = useRef<Map<string, { x: number; y: number }>>(new Map())

  // Stash latest config + nodeRadius in refs so the layout effect doesn't
  // re-trigger when the caller passes inline objects/closures. The original
  // useLiveLayout in ExplorerPanel relied on this stability to keep the
  // prev-position carry working across filter toggles.
  const cfgRef = useRef(config)
  const nodeRadiusRef = useRef(nodeRadius)
  useEffect(() => {
    cfgRef.current = config
    nodeRadiusRef.current = nodeRadius
  })

  useEffect(() => {
    if (!nodes || nodes.length === 0 || width <= 0 || height <= 0) {
      setPositioned([])
      return
    }

    const cfg = { ...DEFAULTS, ...(cfgRef.current ?? {}) }
    const radiusFn = nodeRadiusRef.current ?? (() => 24)

    const sNodes = nodes.map((n) => {
      const prev = prevPosRef.current.get(n.id)
      return {
        ...n,
        x: prev?.x ?? width / 2 + (Math.random() - 0.5) * 80,
        y: prev?.y ?? height / 2 + (Math.random() - 0.5) * 80,
      } as PositionedEngineNode<TNodeData>
    })
    const sLinks = (edges || [])
      .filter(
        (e) =>
          sNodes.some((n) => n.id === e.source) &&
          sNodes.some((n) => n.id === e.target),
      )
      .map((e) => ({ source: e.source, target: e.target }))

    const sim = forceSimulation(sNodes as any)
      .force(
        'link',
        forceLink(sLinks as any)
          .id((d: any) => d.id)
          .distance(cfg.linkDistance)
          .strength(cfg.linkStrength),
      )
      .force('charge', forceManyBody().strength(cfg.charge))
      .force('center', forceCenter(width / 2, height / 2))
      .force(
        'collide',
        forceCollide()
          .radius((d: any) => radiusFn(d) + cfg.collidePadding)
          .iterations(2),
      )
      .alphaDecay(cfg.alphaDecay)
      .alpha(cfg.alpha)

    sim.on('tick', () => {
      setPositioned(sNodes.map((n) => ({ ...n })))
    })

    return () => {
      for (const n of sNodes) {
        prevPosRef.current.set(n.id, { x: n.x, y: n.y })
      }
      sim.stop()
    }
  }, [nodes, edges, width, height])

  return positioned
}
