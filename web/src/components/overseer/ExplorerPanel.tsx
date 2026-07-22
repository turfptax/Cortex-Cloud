/**
 * Polish slice CP1: Data Explorer.
 *
 * Force-directed graph view of the Overseer's interpretive data.
 * Nodes: questions, projects, patterns, drift, themes, episodes,
 * filed gists. Edges: evidence (gist→question), derived_from
 * (pattern/drift→gist), in_project (gist→project).
 *
 * Aesthetic stake (CP1):
 *   - Dark slate background, no grid, no minimap
 *   - Soft filled circles, sized by importance, saturation by confidence
 *   - Edges thin (1px) and slightly transparent, color by relationship
 *   - Hover: lift the node + brighten its incident edges
 *   - Click: focus mode — dim everything not within 2 hops
 *
 * Rendering is delegated to the generic graph engine in
 * `lib/graphengine/`. This file owns the data shape, the aesthetic
 * mapping (colors / opacity / node shape / edge style), and the
 * filter + focus state. The engine owns the camera, force layout,
 * interaction wiring, and the react-flow plumbing.
 */
import { useCallback, useMemo, useState } from 'react'
import {
  GraphCanvas,
  type EngineEdge,
  type EngineNode,
} from '../../lib/graphengine'

// ── Types from the Pi /explorer/graph endpoint ───────────────

export interface GraphNode {
  id: string                 // token, e.g. "q:42"
  type:
    | 'question'
    | 'project'
    | 'pattern'
    | 'drift'
    | 'theme'
    | 'gist'
    | 'episode'
  label: string
  confidence: 'low' | 'med' | 'high'
  size_hint: number
  // CP2: filter-supporting fields
  last_seen?: string | null  // ISO-ish timestamp
  tags?: string[]
  metadata?: Record<string, any>
}

export interface GraphEdge {
  source: string
  target: string
  kind: 'evidence' | 'derived_from' | 'in_project'
  label?: string
  contribution?: string
}

export interface GraphResp {
  ok: boolean
  nodes?: GraphNode[]
  edges?: GraphEdge[]
  stats?: {
    nodes_total: number
    edges_total: number
    by_type: Record<string, number>
  }
  error?: string
}

// ── Aesthetic mapping ────────────────────────────────────────

const TYPE_FILL: Record<GraphNode['type'], string> = {
  question: '#7c5cff',  // accent purple
  project:  '#8da4cc',  // muted blue-grey (utility, not primary)
  pattern:  '#f59e0b',  // amber
  drift:    '#10b981',  // emerald
  theme:    '#a78bfa',  // soft violet
  gist:     '#94a3b8',  // slate
  episode:  '#f472b6',  // rose
}

const TYPE_LABEL: Record<GraphNode['type'], string> = {
  question: 'Question',
  project:  'Project',
  pattern:  'Pattern',
  drift:    'Drift',
  theme:    'Theme',
  gist:     'Gist',
  episode:  'Episode',
}

// CP2: bumped evidence alpha — it's the load-bearing edge for the
// "what feeds what question" reading. Derived_from stays mid; in_
// project stays ambient (otherwise the perimeter project edges
// dominate visually).
const EDGE_COLOR: Record<GraphEdge['kind'], string> = {
  evidence:     '#7c5cffcc',  // purple, ~80% alpha (was 66 / 40%)
  derived_from: '#f59e0b99',  // amber, ~60% alpha (was 55 / 33%)
  in_project:   '#94a3b833',  // slate, very faint (unchanged)
}

const CONF_OPACITY: Record<GraphNode['confidence'], number> = {
  high: 1.0,
  med:  0.7,
  low:  0.45,
}

// Hoisted to module scope so their identity is stable across renders —
// otherwise GraphCanvas's rfNodes useMemo re-runs on every parent render
// and react-flow re-processes all node data each time.
const FORCE_LAYOUT = { kind: 'force' as const }
const BACKGROUND_CONFIG = {
  color: '#0b1018',
  dots: { gap: 24, size: 1, color: '#1e293b' },
}

// Size in px — soft minimum so single-evidence nodes don't disappear,
// log-ish growth so a 30-evidence question doesn't dwarf the canvas.
function nodeRadius(n: GraphNode): number {
  const base = 16
  const bump = Math.min(28, Math.sqrt(Math.max(1, n.size_hint)) * 6)
  return base + bump
}

// ── Node renderer (passed to the engine via renderNode) ──────

function CircleNode({
  graph,
  active,
  dimmed,
}: {
  graph: GraphNode
  active: boolean
  dimmed: boolean
}) {
  const r = nodeRadius(graph)
  const fill = TYPE_FILL[graph.type]
  // Dormant projects (tag set by the backend when last_active >60d)
  // render at 60% confidence so they recede compared to active
  // peers on the same canvas. Doesn't apply to other node types.
  const isDormantProject = graph.type === 'project'
                           && (graph.tags || []).includes('dormant')
  const baseOpacity = isDormantProject
    ? CONF_OPACITY.low
    : CONF_OPACITY[graph.confidence]
  const opacity = (dimmed ? 0.18 : 1) * baseOpacity
  const ring = active ? 3 : 0
  const size = r * 2
  return (
    <div
      style={{
        width: size,
        height: size,
        position: 'relative',
        // Ease-out-quint: slow-then-stop. Reads as "settles" rather
        // than "snaps" — closer to the calm Obsidian feel.
        transition: 'opacity 200ms cubic-bezier(0.22, 1, 0.36, 1)',
        opacity,
      }}
      title={`${TYPE_LABEL[graph.type]} ${graph.id}\n${graph.label}\nconfidence: ${graph.confidence}`}
    >
      <svg
        width={size}
        height={size}
        style={{ position: 'absolute', inset: 0, overflow: 'visible' }}
      >
        {ring > 0 && (
          <circle
            cx={r}
            cy={r}
            r={r + 1.5}
            fill="none"
            stroke={fill}
            strokeWidth={ring}
            opacity={0.85}
          />
        )}
        <circle cx={r} cy={r} r={r} fill={fill} opacity={0.85} />
        <text
          x={r}
          y={r + 4}
          textAnchor="middle"
          fontSize={Math.max(11, r * 0.5)}
          fontWeight="600"
          fill="#0b1018"
          fontFamily="ui-monospace,SFMono-Regular,Menlo,monospace"
          style={{ pointerEvents: 'none' }}
        >
          {graph.id.split(':')[0]}
        </text>
      </svg>
      <div
        style={{
          position: 'absolute',
          left: '50%',
          top: '100%',
          transform: 'translate(-50%, 4px)',
          fontSize: 10,
          color: '#cbd5e1',
          whiteSpace: 'nowrap',
          maxWidth: 180,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          pointerEvents: 'none',
        }}
      >
        {graph.label.length > 40
          ? graph.label.slice(0, 40) + '…'
          : graph.label}
      </div>
    </div>
  )
}

// ── Filter state + predicate ─────────────────────────────────

interface ExplorerFilters {
  search: string
  confidence: Set<GraphNode['confidence']>
  recencyDays: number | null   // null = no recency filter
  questionFocus: string | null // q:N — same effect as click-focus
  hideDisconnected: boolean    // hides nodes with degree 0
}

const DEFAULT_FILTERS: ExplorerFilters = {
  search: '',
  confidence: new Set(['low', 'med', 'high']),
  recencyDays: null,
  questionFocus: null,
  hideDisconnected: true,  // most useful default — Tory had 47 floaters
}

function nodeMatchesFilters(
  n: GraphNode,
  f: ExplorerFilters,
  degree: Map<string, number>,
  focusKeepSet: Set<string> | null,
): boolean {
  // Focus-question filter — when a question is pinned, ONLY show
  // nodes within 2 hops of it. Previously this was a dim-only
  // hint; now it's a real filter (matches user mental model of
  // "focus on this question").
  if (focusKeepSet && !focusKeepSet.has(n.id)) {
    return false
  }
  // Disconnected gate — but ALWAYS show projects, even when alone.
  // Slice 4 made projects the user's primary anchor; many of them
  // legitimately have no question-evidence edges yet.
  if (f.hideDisconnected
      && n.type !== 'project'
      && (degree.get(n.id) || 0) === 0) {
    return false
  }
  // Confidence
  if (!f.confidence.has(n.confidence)) {
    return false
  }
  // Recency — when active, hide nodes older than the cutoff. Nodes
  // without last_seen are treated as "unknown age" and kept.
  if (f.recencyDays != null && n.last_seen) {
    const ts = Date.parse(n.last_seen)
    if (!Number.isNaN(ts)) {
      const cutoff = Date.now() - f.recencyDays * 24 * 60 * 60 * 1000
      if (ts < cutoff) return false
    }
  }
  // Search — matches across label + tags + id token
  if (f.search.trim()) {
    const needle = f.search.trim().toLowerCase()
    const haystack = [
      n.label.toLowerCase(),
      n.id.toLowerCase(),
      ...(n.tags || []).map((t) => t.toLowerCase()),
    ].join(' ')
    if (!haystack.includes(needle)) return false
  }
  return true
}

// ── Empty-state nudge ────────────────────────────────────────
//
// Three cases that all render to "no nodes on canvas":
//   1. Graph hasn't been fetched yet (totalCount === 0)
//   2. Graph has nodes but every active filter combined hides them all
//   3. Search term matches nothing
// Distinguish them so the user knows whether to refresh or to loosen a
// filter — the previous "click Refresh" message lied in cases 2 & 3.

function describeActiveFilters(f: ExplorerFilters): string[] {
  const out: string[] = []
  if (f.search.trim()) out.push(`search "${f.search.trim()}"`)
  if (f.confidence.size < 3) {
    out.push(`confidence: ${[...f.confidence].sort().join(' / ')}`)
  }
  if (f.recencyDays != null) out.push(`recency ≤ ${f.recencyDays}d`)
  if (f.questionFocus) out.push(`focused on ${f.questionFocus}`)
  if (f.hideDisconnected) out.push('hide disconnected')
  return out
}

function ExplorerEmptyState({
  totalCount,
  filters,
  setFilters,
  onRefresh,
}: {
  totalCount: number
  filters: ExplorerFilters
  setFilters: (f: ExplorerFilters) => void
  onRefresh: () => void
}) {
  // Case 1 — nothing fetched.
  if (totalCount === 0) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-sm text-text-muted px-6 text-center">
        <p>No graph data yet.</p>
        <button
          onClick={onRefresh}
          className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent/20 text-accent-hover hover:bg-accent/30 cursor-pointer"
        >
          Refresh
        </button>
      </div>
    )
  }

  // Cases 2 & 3 — graph exists but filters hide it.
  const active = describeActiveFilters(filters)
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-sm text-text-muted px-6 text-center">
      <p>
        All <span className="text-text-secondary font-medium">{totalCount}</span>{' '}
        nodes are hidden by your filters.
      </p>
      {active.length > 0 && (
        <p className="text-[11px] text-text-muted/70 max-w-md">
          Active: {active.join(' · ')}
        </p>
      )}
      <button
        onClick={() => setFilters({ ...DEFAULT_FILTERS })}
        className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer"
      >
        Reset filters
      </button>
    </div>
  )
}

// ── Sidebar ──────────────────────────────────────────────────

function ExplorerSidebar({
  graph,
  filters,
  setFilters,
}: {
  graph: GraphResp | null
  filters: ExplorerFilters
  setFilters: (f: ExplorerFilters) => void
}) {
  const questions = (graph?.nodes || []).filter((n) => n.type === 'question')

  const toggleConfidence = (c: GraphNode['confidence']) => {
    const next = new Set(filters.confidence)
    if (next.has(c)) next.delete(c)
    else next.add(c)
    // Don't allow empty set — at least one must be on. Re-add if
    // user emptied it.
    if (next.size === 0) next.add(c)
    setFilters({ ...filters, confidence: next })
  }

  return (
    <aside
      className="w-56 shrink-0 flex flex-col gap-4 p-4 border-r border-border overflow-y-auto"
      style={{ background: '#0a0e16' }}
    >
      {/* Search */}
      <div>
        <label className="text-[10px] uppercase tracking-wide text-text-muted block mb-1">
          Search
        </label>
        <input
          type="text"
          value={filters.search}
          onChange={(e) => setFilters({ ...filters, search: e.target.value })}
          placeholder="label, tag, or id…"
          className="w-full px-2 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded border border-border focus:outline-none focus:border-accent"
        />
      </div>

      {/* Confidence */}
      <div>
        <label className="text-[10px] uppercase tracking-wide text-text-muted block mb-1">
          Confidence
        </label>
        <div className="flex gap-1">
          {(['high', 'med', 'low'] as const).map((c) => {
            const on = filters.confidence.has(c)
            return (
              <button
                key={c}
                onClick={() => toggleConfidence(c)}
                className={`px-2 py-1 rounded text-[10px] uppercase font-medium cursor-pointer transition-opacity ${
                  on
                    ? 'bg-accent/30 text-accent-hover'
                    : 'bg-surface-tertiary text-text-muted opacity-60'
                }`}
              >
                {c}
              </button>
            )
          })}
        </div>
      </div>

      {/* Recency */}
      <div>
        <label className="text-[10px] uppercase tracking-wide text-text-muted block mb-1">
          Recency
        </label>
        <div className="flex gap-1 flex-wrap">
          {[
            { label: 'all', days: null },
            { label: '90d', days: 90 },
            { label: '30d', days: 30 },
            { label: '7d', days: 7 },
          ].map((opt) => {
            const on = filters.recencyDays === opt.days
            return (
              <button
                key={opt.label}
                onClick={() => setFilters({ ...filters, recencyDays: opt.days })}
                className={`px-2 py-1 rounded text-[10px] uppercase font-medium cursor-pointer ${
                  on
                    ? 'bg-accent/30 text-accent-hover'
                    : 'bg-surface-tertiary text-text-muted'
                }`}
              >
                {opt.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* Question focus — filters to 2-hop neighborhood */}
      {questions.length > 0 && (
        <div>
          <label className="text-[10px] uppercase tracking-wide text-text-muted block mb-1">
            Filter to question
          </label>
          <select
            value={filters.questionFocus || ''}
            onChange={(e) =>
              setFilters({
                ...filters,
                questionFocus: e.target.value || null,
              })
            }
            className="w-full px-2 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded border border-border focus:outline-none focus:border-accent"
          >
            <option value="">— none —</option>
            {questions.map((q) => (
              <option key={q.id} value={q.id}>
                {q.id}: {q.label.length > 32 ? q.label.slice(0, 32) + '…' : q.label}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Hide disconnected */}
      <div>
        <label className="flex items-center gap-2 text-[11px] text-text-secondary cursor-pointer">
          <input
            type="checkbox"
            checked={filters.hideDisconnected}
            onChange={(e) =>
              setFilters({ ...filters, hideDisconnected: e.target.checked })
            }
          />
          Hide disconnected nodes
        </label>
      </div>

      {/* Reset */}
      <button
        onClick={() => setFilters({ ...DEFAULT_FILTERS })}
        className="text-[10px] uppercase text-text-muted hover:text-text-primary cursor-pointer self-start"
      >
        Reset filters
      </button>

      {/* Legend (moved from topbar) */}
      <div className="mt-auto pt-4 border-t border-border">
        <div className="text-[10px] uppercase tracking-wide text-text-muted mb-2">
          Legend
        </div>
        <ul className="space-y-1 text-[11px] text-text-secondary">
          {(['question', 'pattern', 'drift', 'theme', 'episode', 'gist', 'project'] as const).map((t) => (
            <li key={t} className="flex items-center gap-2">
              <span
                style={{
                  background: TYPE_FILL[t],
                  width: 10,
                  height: 10,
                  borderRadius: 999,
                  display: 'inline-block',
                }}
              />
              {TYPE_LABEL[t]}
            </li>
          ))}
        </ul>
        <div className="mt-3 text-[10px] text-text-muted leading-relaxed">
          single-click to focus<br />
          double-click to drill in
        </div>
      </div>
    </aside>
  )
}

// ── Main panel ───────────────────────────────────────────────

export function ExplorerPanel({
  graph,
  loading,
  error,
  onRefresh,
  onTokenClick,
}: {
  graph: GraphResp | null
  loading: boolean
  error: string
  onRefresh: () => void
  onTokenClick: (token: string) => void
}) {
  const [focusId, setFocusId] = useState<string | null>(null)
  const [filters, setFilters] = useState<ExplorerFilters>({
    ...DEFAULT_FILTERS,
  })

  // Effective focus = filters.questionFocus takes precedence over click
  // focus, so the user can pin a question and still single-click to
  // explore other nodes ad hoc. Clicking a node OVERRIDES the pinned
  // focus only until they clear-click.
  const effectiveFocus = focusId || filters.questionFocus

  // Degree map (for hide-disconnected). Recomputed when graph changes.
  const degree = useMemo<Map<string, number>>(() => {
    const m = new Map<string, number>()
    if (!graph) return m
    for (const e of graph.edges || []) {
      m.set(e.source, (m.get(e.source) || 0) + 1)
      m.set(e.target, (m.get(e.target) || 0) + 1)
    }
    return m
  }, [graph])

  // Adjacency — used by both the focus-question filter (2-hop keep
  // set) and the click-focus dim set below.
  const adjacency = useMemo<Map<string, Set<string>>>(() => {
    const adj = new Map<string, Set<string>>()
    if (!graph) return adj
    for (const e of graph.edges || []) {
      if (!adj.has(e.source)) adj.set(e.source, new Set())
      if (!adj.has(e.target)) adj.set(e.target, new Set())
      adj.get(e.source)!.add(e.target)
      adj.get(e.target)!.add(e.source)
    }
    return adj
  }, [graph])

  // Focus-question keep set: when the user has PINNED a question via
  // the dropdown, only nodes within 2 hops survive the visibility
  // filter. Click-focus (focusId) is a separate UX — that one only
  // dims, it doesn't filter, since it's meant for ad-hoc poking.
  const focusKeepSet = useMemo<Set<string> | null>(() => {
    if (!filters.questionFocus || !graph) return null
    const start = filters.questionFocus
    if (!graph.nodes?.some((n) => n.id === start)) return null
    const keep = new Set<string>([start])
    let frontier: string[] = [start]
    for (let depth = 0; depth < 2 && frontier.length; depth++) {
      const next: string[] = []
      for (const id of frontier) {
        for (const n of adjacency.get(id) || []) {
          if (!keep.has(n)) {
            keep.add(n)
            next.push(n)
          }
        }
      }
      frontier = next
    }
    return keep
  }, [filters.questionFocus, graph, adjacency])

  const visibleSet = useMemo<Set<string>>(() => {
    if (!graph) return new Set()
    const out = new Set<string>()
    for (const n of graph.nodes || []) {
      if (nodeMatchesFilters(n, filters, degree, focusKeepSet)) {
        out.add(n.id)
      }
    }
    return out
  }, [graph, filters, degree, focusKeepSet])

  const visibleNodes = useMemo(
    () => (graph?.nodes || []).filter((n) => visibleSet.has(n.id)),
    [graph, visibleSet],
  )
  const visibleEdges = useMemo(
    () =>
      (graph?.edges || []).filter(
        (e) => visibleSet.has(e.source) && visibleSet.has(e.target),
      ),
    [graph, visibleSet],
  )

  // Click-focus dim set: ad-hoc single-click on a node dims everything
  // outside 2 hops (so the user can quickly trace a node's
  // neighborhood without losing context). Only fires for click-focus
  // (focusId) — the dropdown-pinned questionFocus filters instead,
  // computed in focusKeepSet above.
  const dimSet = useMemo<Set<string>>(() => {
    if (!focusId || !graph) return new Set()
    const keep = new Set<string>([focusId])
    let frontier: string[] = [focusId]
    for (let depth = 0; depth < 2 && frontier.length; depth++) {
      const next: string[] = []
      for (const id of frontier) {
        for (const n of adjacency.get(id) || []) {
          if (!keep.has(n)) {
            keep.add(n)
            next.push(n)
          }
        }
      }
      frontier = next
    }
    const dim = new Set<string>()
    for (const id of visibleSet) if (!keep.has(id)) dim.add(id)
    return dim
  }, [focusId, graph, visibleSet, adjacency])

  // ── Adapt to engine shape ────────────────────────────────────
  const engineNodes = useMemo<EngineNode<GraphNode>[]>(
    () => visibleNodes.map((n) => ({ id: n.id, data: n })),
    [visibleNodes],
  )
  const engineEdges = useMemo<EngineEdge<GraphEdge>[]>(
    () =>
      visibleEdges.map((e) => ({ source: e.source, target: e.target, data: e })),
    [visibleEdges],
  )

  // Stable engine callbacks. Inline closures here would change identity
  // every parent render, forcing GraphCanvas to re-memo rfNodes and
  // react-flow to re-process every node — visible as a long lag and
  // mass DOM mutation on every click/filter change.
  const renderNode = useCallback(
    (
      node: EngineNode<GraphNode>,
      { active, dimmed }: { active: boolean; dimmed: boolean },
    ) => <CircleNode graph={node.data} active={active} dimmed={dimmed} />,
    [],
  )
  const edgeStyle = useCallback(
    (
      edge: EngineEdge<GraphEdge>,
      { highlighted, dimmed }: { highlighted: boolean; dimmed: boolean },
    ) => ({
      stroke: EDGE_COLOR[edge.data!.kind],
      // Dimmed edges shrink AND fade — pulls them visually behind the
      // foreground 2-hop subgraph in focus mode.
      strokeWidth: highlighted ? 2 : dimmed ? 0.75 : 1,
      opacity: dimmed ? 0.15 : 1,
      transition:
        'opacity 200ms cubic-bezier(0.22, 1, 0.36, 1), ' +
        'stroke-width 200ms cubic-bezier(0.22, 1, 0.36, 1)',
    }),
    [],
  )
  const nodeSize = useCallback((node: EngineNode<GraphNode>) => {
    const r = nodeRadius(node.data)
    return { w: r * 2, h: r * 2 }
  }, [])
  const nodeRadiusForCollision = useCallback(
    (node: EngineNode<GraphNode>) => nodeRadius(node.data),
    [],
  )
  const handleNodeClick = useCallback((id: string) => {
    setFocusId((cur) => (cur === id ? null : id))
  }, [])
  const handleNodeDoubleClick = useCallback(
    (id: string) => {
      onTokenClick(id)
    },
    [onTokenClick],
  )
  const handlePaneClick = useCallback(() => setFocusId(null), [])

  // Visible/total node count for the topbar.
  const visibleCount = visibleSet.size
  const totalCount = graph?.nodes?.length || 0

  return (
    <div className="flex-1 flex" style={{ background: '#0b1018' }}>
      <ExplorerSidebar
        graph={graph}
        filters={filters}
        setFilters={setFilters}
      />
      <div className="flex-1 flex flex-col">
        {/* Top bar — fixed height so the optional "Clear focus" button
            appearing on click doesn't reflow the canvas (which would
            trigger ResizeObserver → useForceLayout restart → sim reboot
            and a visible "everything disappears" jank on every click). */}
        <div className="flex items-center gap-3 px-4 py-2 border-b border-border min-w-0 h-11 shrink-0">
          <h3 className="text-sm font-semibold text-text-primary whitespace-nowrap">
            Explorer
          </h3>
          <span className="text-xs text-text-muted whitespace-nowrap truncate">
            {graph?.stats
              ? visibleCount === totalCount
                ? `${totalCount} nodes · ${graph.stats.edges_total} edges`
                : `${visibleCount} of ${totalCount} nodes shown`
              : loading
                ? 'loading…'
                : ''}
          </span>
          {effectiveFocus && (
            <button
              onClick={() => {
                setFocusId(null)
                setFilters({ ...filters, questionFocus: null })
              }}
              className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-secondary cursor-pointer whitespace-nowrap"
            >
              Clear focus ({effectiveFocus})
            </button>
          )}
          <button
            onClick={onRefresh}
            disabled={loading}
            className="ml-auto px-3 py-1 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>

        {/* Canvas */}
        <div className="flex-1 relative">
          {error && (
            <div className="absolute inset-0 flex items-center justify-center text-sm text-red-400 z-10">
              Error: {error}
            </div>
          )}
          {!loading && !error && engineNodes.length === 0 && (
            <ExplorerEmptyState
              totalCount={totalCount}
              filters={filters}
              setFilters={setFilters}
              onRefresh={onRefresh}
            />
          )}
          <GraphCanvas<GraphNode, GraphEdge>
            nodes={engineNodes}
            edges={engineEdges}
            activeNodeId={effectiveFocus}
            dimmedNodeIds={dimSet}
            renderNode={renderNode}
            edgeStyle={edgeStyle}
            nodeSize={nodeSize}
            nodeRadiusForCollision={nodeRadiusForCollision}
            layout={FORCE_LAYOUT}
            minZoom={0.2}
            maxZoom={2.5}
            // CP2 Tory feedback: wheel = zoom (Obsidian-feel).
            // panOnScroll=false makes the wheel always zoom rather
            // than pan; click-drag the canvas to pan.
            panOnScroll={false}
            panOnDrag
            zoomOnScroll
            zoomOnPinch
            background={BACKGROUND_CONFIG}
            onNodeClick={handleNodeClick}
            onNodeDoubleClick={handleNodeDoubleClick}
            onPaneClick={handlePaneClick}
          />
        </div>
      </div>
    </div>
  )
}
