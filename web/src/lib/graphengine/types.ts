import type { CSSProperties, ReactNode } from 'react'

export interface EngineNode<TData = unknown> {
  id: string
  data: TData
}

export interface PositionedEngineNode<TData = unknown> extends EngineNode<TData> {
  x: number
  y: number
}

export interface EngineEdge<TData = unknown> {
  source: string
  target: string
  data?: TData
}

export interface NodeRenderState {
  active: boolean
  dimmed: boolean
}

export interface EdgeRenderState {
  highlighted: boolean
  dimmed: boolean
}

export type RenderNode<TNodeData = unknown> = (
  node: EngineNode<TNodeData>,
  state: NodeRenderState,
) => ReactNode

export type EdgeStyle<TEdgeData = unknown> = (
  edge: EngineEdge<TEdgeData>,
  state: EdgeRenderState,
) => CSSProperties

export type NodeSize<TNodeData = unknown> = (
  node: EngineNode<TNodeData>,
) => { w: number; h: number }

export interface ForceLayoutConfig {
  kind: 'force'
  alphaDecay?: number
  alpha?: number
  linkDistance?: number
  linkStrength?: number
  charge?: number
  collidePadding?: number
}

export type LayoutConfig = ForceLayoutConfig

export interface BackgroundConfig {
  color?: string
  dots?: {
    gap?: number
    size?: number
    color?: string
  } | null
}
