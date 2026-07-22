import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BaseEdge,
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getStraightPath,
  useInternalNode,
  type Edge as RFEdge,
  type EdgeProps,
  type Node as RFNode,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type {
  BackgroundConfig,
  EdgeStyle,
  EngineEdge,
  EngineNode,
  LayoutConfig,
  NodeSize,
  RenderNode,
} from './types'
import { useForceLayout } from './useForceLayout'

export interface GraphCanvasProps<TNodeData = unknown, TEdgeData = unknown> {
  nodes: EngineNode<TNodeData>[]
  edges: EngineEdge<TEdgeData>[]
  /** Node currently focused — passed to renderNode as state.active. */
  activeNodeId?: string | null
  /** Node ids to render in a dimmed state. */
  dimmedNodeIds?: Set<string>
  /** Render the visual contents of each node. Engine wraps this with invisible edge anchors. */
  renderNode: RenderNode<TNodeData>
  /** Per-edge style. Engine computes `highlighted` (touches active) and `dimmed` (active set & doesn't touch). */
  edgeStyle: EdgeStyle<TEdgeData>
  /** Visual size of each node — used for the engine's center-anchor offset. */
  nodeSize: NodeSize<TNodeData>
  /** Layout strategy. */
  layout: LayoutConfig
  /** Per-node collision radius for the force sim. Defaults to max(width, height) / 2. */
  nodeRadiusForCollision?: (node: EngineNode<TNodeData>) => number
  // Camera / interaction
  minZoom?: number
  maxZoom?: number
  panOnScroll?: boolean
  panOnDrag?: boolean
  zoomOnScroll?: boolean
  zoomOnPinch?: boolean
  fitView?: boolean
  // Chrome
  background?: BackgroundConfig
  showControls?: boolean
  // Events
  onNodeClick?: (id: string, event: React.MouseEvent) => void
  onNodeDoubleClick?: (id: string, event: React.MouseEvent) => void
  onPaneClick?: () => void
}

interface InternalNodeData<TNodeData> {
  node: EngineNode<TNodeData>
  active: boolean
  dimmed: boolean
  size: { w: number; h: number }
  render: RenderNode<TNodeData>
}

function EngineNodeRenderer({ data }: NodeProps) {
  const { node, active, dimmed, size, render } =
    data as unknown as InternalNodeData<unknown>
  return (
    <div style={{ width: size.w, height: size.h, position: 'relative' }}>
      <Handle
        type="source"
        position={Position.Top}
        style={{ opacity: 0, pointerEvents: 'none' }}
      />
      <Handle
        type="target"
        position={Position.Bottom}
        style={{ opacity: 0, pointerEvents: 'none' }}
      />
      {render(node, { active, dimmed })}
    </div>
  )
}

const nodeTypes = { engine: EngineNodeRenderer }

// ── Floating edge ─────────────────────────────────────────────
//
// react-flow's built-in edge types route between fixed Handle positions
// (here: source.top → target.bottom). For a force-directed graph where
// nodes can sit anywhere relative to each other, that means edges
// always exit the top of the source and enter the bottom of the
// target — visually "crossed" or off-perimeter when a source is to the
// left or right of its target. The Obsidian-feel fix is to compute
// each endpoint as the point where the line between node centers
// crosses the node's own circular perimeter.
//
// Assumes circular nodes; uses the node's smaller dimension as the
// radius. For non-circular shapes we'd swap in a bounding-box
// intersection but the engine's caller is responsible for shape.

interface NodeCircle {
  cx: number
  cy: number
  r: number
}

function nodeCircle(
  node: { position: { x: number; y: number }; measured?: { width?: number; height?: number }; width?: number; height?: number } | null | undefined,
): NodeCircle | null {
  if (!node) return null
  const w = node.measured?.width ?? node.width ?? 0
  const h = node.measured?.height ?? node.height ?? 0
  if (w === 0 || h === 0) return null
  return {
    cx: node.position.x + w / 2,
    cy: node.position.y + h / 2,
    r: Math.min(w, h) / 2,
  }
}

function circlePerimeterPoint(
  from: NodeCircle,
  to: { cx: number; cy: number },
): { x: number; y: number } {
  const dx = to.cx - from.cx
  const dy = to.cy - from.cy
  const len = Math.hypot(dx, dy)
  if (len === 0) return { x: from.cx, y: from.cy }
  return {
    x: from.cx + (dx * from.r) / len,
    y: from.cy + (dy * from.r) / len,
  }
}

function FloatingEdge({ id, source, target, style, markerEnd }: EdgeProps) {
  const sourceNode = useInternalNode(source)
  const targetNode = useInternalNode(target)
  const s = nodeCircle(sourceNode as any)
  const t = nodeCircle(targetNode as any)
  if (!s || !t) return null
  const sourcePoint = circlePerimeterPoint(s, t)
  const targetPoint = circlePerimeterPoint(t, s)
  const [path] = getStraightPath({
    sourceX: sourcePoint.x,
    sourceY: sourcePoint.y,
    targetX: targetPoint.x,
    targetY: targetPoint.y,
  })
  return <BaseEdge id={id} path={path} style={style} markerEnd={markerEnd} />
}

const edgeTypes = { floating: FloatingEdge }

function GraphCanvasInner<TNodeData, TEdgeData>(
  props: GraphCanvasProps<TNodeData, TEdgeData>,
) {
  const {
    nodes,
    edges,
    activeNodeId = null,
    dimmedNodeIds,
    renderNode,
    edgeStyle,
    nodeSize,
    layout,
    nodeRadiusForCollision,
    minZoom = 0.2,
    maxZoom = 2.5,
    panOnScroll = false,
    panOnDrag = true,
    zoomOnScroll = true,
    zoomOnPinch = true,
    fitView = true,
    background,
    showControls = true,
    onNodeClick,
    onNodeDoubleClick,
    onPaneClick,
  } = props

  const containerRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 1200, h: 800 })

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    // Round to integers and skip updates when size hasn't actually changed.
    // Sub-pixel reflows (e.g. a topbar button appearing on focus) would
    // otherwise restart the force sim on every click via useForceLayout's
    // [width, height] deps.
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect()
      const w = Math.round(r.width)
      const h = Math.round(r.height)
      setSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const collisionRadius = useMemo(() => {
    if (nodeRadiusForCollision) return nodeRadiusForCollision
    return (n: EngineNode<TNodeData>) => {
      const s = nodeSize(n)
      return Math.max(s.w, s.h) / 2
    }
  }, [nodeRadiusForCollision, nodeSize])

  const positioned = useForceLayout<TNodeData>({
    nodes: layout.kind === 'force' ? nodes : null,
    edges: layout.kind === 'force' ? (edges as EngineEdge[]) : null,
    width: size.w,
    height: size.h,
    config: layout.kind === 'force' ? layout : undefined,
    nodeRadius: collisionRadius,
  })

  const rfNodes = useMemo<RFNode[]>(() => {
    return positioned.map((p) => {
      const s = nodeSize(p)
      const data: InternalNodeData<TNodeData> = {
        node: p,
        active: activeNodeId === p.id,
        dimmed: dimmedNodeIds?.has(p.id) ?? false,
        size: s,
        render: renderNode,
      }
      return {
        id: p.id,
        type: 'engine',
        position: { x: p.x - s.w / 2, y: p.y - s.h / 2 },
        // Set width/height explicitly so FloatingEdge can compute
        // perimeter intersections on the first frame, before
        // react-flow's async DOM measurement settles.
        width: s.w,
        height: s.h,
        data: data as unknown as Record<string, unknown>,
        draggable: false,
        selectable: false,
      }
    })
  }, [positioned, activeNodeId, dimmedNodeIds, renderNode, nodeSize])

  const rfEdges = useMemo<RFEdge[]>(() => {
    return edges.map((e, i) => {
      const touchesActive =
        !!activeNodeId && (e.source === activeNodeId || e.target === activeNodeId)
      const highlighted = touchesActive
      const dimmed = !!activeNodeId && !touchesActive
      return {
        id: `e-${i}`,
        source: e.source,
        target: e.target,
        type: 'floating',
        animated: false,
        style: edgeStyle(e, { highlighted, dimmed }),
      }
    })
  }, [edges, activeNodeId, edgeStyle])

  const bgColor = background?.color ?? '#0b1018'
  const dotsCfg =
    background?.dots === null
      ? null
      : (background?.dots ?? { gap: 24, size: 1, color: '#1e293b' })

  return (
    <div
      ref={containerRef}
      className="h-full w-full relative"
      style={{ background: bgColor }}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView={fitView}
        minZoom={minZoom}
        maxZoom={maxZoom}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnScroll={panOnScroll}
        panOnDrag={panOnDrag}
        zoomOnScroll={zoomOnScroll}
        zoomOnPinch={zoomOnPinch}
        onNodeClick={(ev, n) =>
          onNodeClick?.(n.id, ev as unknown as React.MouseEvent)
        }
        onNodeDoubleClick={(ev, n) =>
          onNodeDoubleClick?.(n.id, ev as unknown as React.MouseEvent)
        }
        onPaneClick={() => onPaneClick?.()}
        colorMode="dark"
      >
        {dotsCfg && (
          <Background
            variant={BackgroundVariant.Dots}
            gap={dotsCfg.gap ?? 24}
            size={dotsCfg.size ?? 1}
            color={dotsCfg.color ?? '#1e293b'}
          />
        )}
        {showControls && (
          <Controls
            position="bottom-right"
            showInteractive={false}
            style={{
              background: '#0f172a',
              border: '1px solid #1e293b',
              borderRadius: 6,
            }}
          />
        )}
      </ReactFlow>
    </div>
  )
}

export function GraphCanvas<TNodeData = unknown, TEdgeData = unknown>(
  props: GraphCanvasProps<TNodeData, TEdgeData>,
) {
  return (
    <ReactFlowProvider>
      <GraphCanvasInner {...props} />
    </ReactFlowProvider>
  )
}
