/**
 * Slice 10.4 Phase 2: Activity tab — per-run trace viewer.
 *
 * Unified timeline of what overseer ACTUALLY did across all
 * surfaces (B/C agents, A-tier siblings, chat turns, journal
 * steps). Three panels:
 *   - Left: filterable timeline list (24h rolling window default)
 *   - Center: React Flow graph of the selected run
 *   - Right: detail sidebar with full prompt + output + rating UI
 *
 * Companion to EcosystemMapPanel (the Map tab shows what overseer
 * CAN do; Activity shows what they DID).
 *
 * Export button at top triggers a JSON bundle download of the
 * entire visible window — for offline review or bug reports.
 * CP3.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  GraphCanvas,
  type EngineEdge,
  type EngineNode,
} from '../../lib/graphengine'
import { apiFetch } from '../../lib/api'
import { fmtTime } from '../../lib/time'

// ── Types (mirror backend response shapes) ──────────────────────

type RunKind =
  | 'b_agent'
  | 'c_agent'
  | 'sibling'
  | 'chat_turn'
  | 'journal_step'

interface RunListItem {
  id: string
  kind: RunKind
  subkind?: string
  started_at: string
  ended_at: string
  summary: string
  cost_usd: number
  latency_ms: number
  tool_calls_count: number
  model: string
  rateable: boolean
  sibling_task_id: number | null
  current_rating: number | null
  status?: string
  provisionality?: string
}

interface RunsListResp {
  ok: boolean
  hours: number
  count: number
  runs: RunListItem[]
  error?: string
}

interface FlowNode {
  id: string
  kind: 'trigger' | 'snapshot' | 'llm_call' | 'tool_call' | 'output' | 'step'
  label: string
  sublabel?: string
}

interface FlowEdge {
  source: string
  target: string
}

interface RunDetailResp {
  ok: boolean
  id: string
  kind: RunKind
  subkind?: string
  started_at: string
  ended_at: string
  cost_usd: number
  latency_ms: number
  model: string
  nodes: FlowNode[]
  edges: FlowEdge[]
  full_prompt: string
  full_output: string
  tool_calls?: any[]
  rateable: boolean
  sibling_task_id?: number | null
  current_rating?: number | null
  current_notes?: string | null
  status?: string
  provisionality?: string
  raw?: any
  error?: string
}

// ── Visual mapping ──────────────────────────────────────────────

const KIND_BADGE: Record<RunKind, { label: string; color: string }> = {
  b_agent:      { label: 'B', color: '#10b981' },
  c_agent:      { label: 'C', color: '#f59e0b' },
  sibling:      { label: 'A', color: '#ef4444' },
  chat_turn:    { label: 'Chat', color: '#3b82f6' },
  journal_step: { label: 'Jrnl', color: '#a78bfa' },
}

const NODE_FILL: Record<FlowNode['kind'], string> = {
  trigger:   '#06b6d4',
  snapshot:  '#475569',
  llm_call:  '#8b5cf6',
  tool_call: '#94a3b8',
  output:    '#10b981',
  step:      '#64748b',
}

// ── Main component ──────────────────────────────────────────────

export function ActivityPanel() {
  const [runs, setRuns] = useState<RunListItem[]>([])
  const [hours, setHours] = useState(24)
  const [kindFilter, setKindFilter] = useState<Set<RunKind>>(new Set([
    'b_agent', 'c_agent', 'sibling', 'chat_turn', 'journal_step',
  ]))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<RunDetailResp | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [activeNodeId, setActiveNodeId] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        hours: String(hours),
        limit: '300',
      })
      const j = await apiFetch<RunsListResp>(
        `/overseer/runs/recent?${params.toString()}`,
      )
      if (!j.ok) throw new Error(j.error || 'runs/recent failed')
      setRuns(j.runs || [])
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }, [hours])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Load detail when a run is selected
  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    const [kind, idStr] = selectedId.split(':', 2)
    // Map id prefix back to backend kind
    const kindMap: Record<string, RunKind> = {
      'b-trans': 'b_agent', // will be re-resolved server-side
      sibling: 'sibling',
      chat: 'chat_turn',
      journal: 'journal_step',
    }
    // Find the run's actual kind from the list (b-trans could be b or c)
    const listItem = runs.find((r) => r.id === selectedId)
    const actualKind = listItem?.kind || kindMap[kind] || 'sibling'
    const params = new URLSearchParams({
      kind: actualKind,
      id: idStr,
    })
    setDetailLoading(true)
    setActiveNodeId(null)
    apiFetch<RunDetailResp>(`/overseer/runs/detail?${params.toString()}`)
      .then((d) => {
        if (!d.ok) {
          setError(d.error || 'detail failed')
          setDetail(null)
        } else {
          setDetail(d)
        }
      })
      .catch((e) => setError(String(e)))
      .finally(() => setDetailLoading(false))
  }, [selectedId, runs])

  const filteredRuns = useMemo(
    () => runs.filter((r) => kindFilter.has(r.kind)),
    [runs, kindFilter],
  )

  const toggleKind = (k: RunKind) => {
    const next = new Set(kindFilter)
    if (next.has(k)) next.delete(k)
    else next.add(k)
    setKindFilter(next)
  }

  // ── Graph nodes / edges from detail ────────────────────────────

  const graphNodes = useMemo<EngineNode<FlowNode>[]>(() => {
    if (!detail?.nodes) return []
    return detail.nodes.map((n) => ({ id: n.id, data: n }))
  }, [detail])

  const graphEdges = useMemo<EngineEdge[]>(() => {
    if (!detail?.edges) return []
    return detail.edges.map((e) => ({ source: e.source, target: e.target }))
  }, [detail])

  const renderNode = useCallback(
    (
      node: EngineNode<FlowNode>,
      state: { active: boolean; dimmed: boolean },
    ) => {
      const d = node.data
      const fill = NODE_FILL[d.kind] || '#64748b'
      const opacity = state.dimmed ? 0.3 : 1
      const ring = state.active ? '0 0 0 3px #f8fafc' : 'none'
      return (
        <div
          style={{
            width: 170,
            minHeight: 50,
            background: fill,
            opacity,
            boxShadow: ring,
            borderRadius: 8,
            padding: '6px 10px',
            color: '#0f172a',
            fontSize: 11,
            cursor: 'pointer',
          }}
        >
          <div style={{ fontWeight: 600, lineHeight: 1.2 }}>{d.label}</div>
          {d.sublabel && (
            <div
              style={{
                fontSize: 10,
                opacity: 0.85,
                marginTop: 2,
                lineHeight: 1.2,
              }}
            >
              {d.sublabel}
            </div>
          )}
        </div>
      )
    },
    [],
  )

  const edgeStyle = useCallback(
    (_: EngineEdge, state: { highlighted: boolean; dimmed: boolean }) => ({
      stroke: '#64748b',
      strokeWidth: state.highlighted ? 2 : 1.2,
      opacity: state.dimmed ? 0.2 : 0.7,
    }),
    [],
  )

  const nodeSize = useCallback(() => ({ w: 170, h: 50 }), [])

  // ── 24h bundle export ─────────────────────────────────────────

  const handleExport = useCallback(async () => {
    setExporting(true)
    try {
      const r = await fetch(
        `/api/overseer/runs/export?hours=${hours}`,
      )
      const text = await r.text()
      // Trigger download
      const blob = new Blob([text], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      const ts = new Date()
        .toISOString()
        .replace(/[:T]/g, '-')
        .slice(0, 16)
      a.href = url
      a.download = `overseer_runs_${ts}_${hours}h.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e: any) {
      setError(`Export failed: ${e?.message || e}`)
    } finally {
      setExporting(false)
    }
  }, [hours])

  // ── Render ──────────────────────────────────────────────────────

  return (
    <div className="flex h-full" style={{ minHeight: 700 }}>
      {/* Timeline list */}
      <div
        className="border-r border-border-subtle bg-surface-secondary overflow-y-auto flex flex-col"
        style={{ width: 320 }}
      >
        <div className="p-3 border-b border-border-subtle space-y-2 sticky top-0 bg-surface-secondary z-10">
          <div className="flex items-center gap-2">
            <span className="text-xs text-text-muted">Window:</span>
            <select
              value={hours}
              onChange={(e) => setHours(parseInt(e.target.value))}
              className="bg-surface-tertiary text-text-primary text-xs px-2 py-1 rounded"
            >
              <option value="1">1h</option>
              <option value="6">6h</option>
              <option value="24">24h</option>
              <option value="72">3d</option>
              <option value="168">7d</option>
            </select>
            <button
              onClick={refresh}
              disabled={loading}
              className="text-xs px-2 py-1 bg-surface-tertiary hover:bg-surface-tertiary/70 rounded"
            >
              {loading ? '…' : 'Refresh'}
            </button>
          </div>
          <div className="flex flex-wrap gap-1">
            {(Object.keys(KIND_BADGE) as RunKind[]).map((k) => (
              <button
                key={k}
                onClick={() => toggleKind(k)}
                className={`text-[10px] px-2 py-0.5 rounded font-semibold transition-opacity ${
                  kindFilter.has(k) ? 'opacity-100' : 'opacity-30'
                }`}
                style={{
                  background: KIND_BADGE[k].color,
                  color: '#0f172a',
                }}
              >
                {KIND_BADGE[k].label}
              </button>
            ))}
          </div>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="w-full text-xs px-2 py-1.5 bg-accent hover:bg-accent-hover text-white rounded disabled:opacity-50 cursor-pointer"
          >
            {exporting ? 'Exporting…' : `⬇ Export ${hours}h bundle (JSON)`}
          </button>
          <div className="text-xs text-text-muted">
            {filteredRuns.length} of {runs.length} runs
          </div>
        </div>

        {error && (
          <div className="m-3 p-2 bg-red-900/30 border border-red-700 rounded text-xs text-red-300">
            {error}
          </div>
        )}

        <div className="flex-1">
          {filteredRuns.map((r) => (
            <RunListRow
              key={r.id}
              run={r}
              selected={r.id === selectedId}
              onClick={() => setSelectedId(r.id)}
            />
          ))}
          {!filteredRuns.length && !loading && (
            <div className="p-4 text-xs text-text-muted">
              No runs match the current filter.
            </div>
          )}
        </div>
      </div>

      {/* Center: flow graph */}
      <div className="flex-1 relative">
        {!detail && !detailLoading && (
          <div className="flex items-center justify-center h-full text-text-muted text-sm">
            <div className="text-center max-w-md">
              <div className="mb-2 font-semibold text-text-secondary">
                Activity
              </div>
              <p>
                Select a run on the left to see its flow graph + full prompt /
                output / rating.
              </p>
              <p className="mt-3 text-xs">
                The Map tab shows what overseer CAN do; this tab shows what
                they actually DID. Each run renders as a graph: trigger →
                LLM calls → tool calls → output. Click any node to drill into
                that step in the detail sidebar.
              </p>
            </div>
          </div>
        )}
        {detailLoading && (
          <div className="flex items-center justify-center h-full text-text-muted text-sm">
            Loading…
          </div>
        )}
        {detail && detail.nodes && (
          <>
            <GraphCanvas
              nodes={graphNodes}
              edges={graphEdges}
              activeNodeId={activeNodeId}
              renderNode={renderNode}
              edgeStyle={edgeStyle}
              nodeSize={nodeSize}
              layout={{
                kind: 'force',
                linkDistance: 110,
                linkStrength: 0.3,
                charge: -400,
                collidePadding: 10,
                alphaDecay: 0.04,
              }}
              background={{
                color: '#0f172a',
                dots: { gap: 24, size: 1, color: '#1e293b' },
              }}
              onNodeClick={(id) => setActiveNodeId(id)}
              onPaneClick={() => setActiveNodeId(null)}
            />
            {/* Run summary overlay */}
            <div className="absolute top-3 left-3 bg-surface-secondary/95 backdrop-blur rounded-lg px-3 py-2 text-xs space-y-1">
              <div className="flex items-center gap-2">
                <span
                  className="px-2 py-0.5 rounded text-[10px] font-semibold"
                  style={{
                    background: KIND_BADGE[detail.kind].color,
                    color: '#0f172a',
                  }}
                >
                  {KIND_BADGE[detail.kind].label}
                </span>
                <span className="text-text-secondary font-mono">
                  {detail.id}
                </span>
              </div>
              <div className="text-text-muted">
                {fmtTime(detail.started_at)} · {detail.model.split('/').pop() || '?'}
              </div>
              <div className="text-text-muted">
                ${detail.cost_usd.toFixed(4)} · {detail.latency_ms}ms ·{' '}
                {detail.nodes.length} nodes
              </div>
            </div>
          </>
        )}
      </div>

      {/* Detail sidebar */}
      <div
        className="border-l border-border-subtle bg-surface-secondary overflow-y-auto"
        style={{ width: 380 }}
      >
        {detail && (
          <RunDetailSidebar
            detail={detail}
            activeNodeId={activeNodeId}
            onRated={refresh}
          />
        )}
        {!detail && (
          <div className="p-4 text-xs text-text-muted">
            Run details + rating UI will appear here.
          </div>
        )}
      </div>
    </div>
  )
}

// ── Timeline list row ───────────────────────────────────────────

function RunListRow({
  run,
  selected,
  onClick,
}: {
  run: RunListItem
  selected: boolean
  onClick: () => void
}) {
  const badge = KIND_BADGE[run.kind]
  return (
    <div
      onClick={onClick}
      className={`border-b border-border-subtle px-3 py-2 cursor-pointer transition-colors ${
        selected
          ? 'bg-accent/20'
          : 'hover:bg-surface-tertiary/50'
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <span
          className="px-1.5 py-0.5 rounded text-[10px] font-bold leading-tight"
          style={{ background: badge.color, color: '#0f172a' }}
        >
          {badge.label}
        </span>
        <span className="text-xs text-text-muted">
          {fmtTime(run.started_at)}
        </span>
        {run.tool_calls_count > 0 && (
          <span className="text-[10px] text-text-muted">
            {run.tool_calls_count}🔧
          </span>
        )}
        {run.cost_usd > 0 && (
          <span className="text-[10px] text-text-muted ml-auto">
            ${run.cost_usd.toFixed(3)}
          </span>
        )}
      </div>
      <div className="text-xs text-text-secondary leading-snug line-clamp-2">
        {run.summary || '(empty)'}
      </div>
      <div className="flex items-center gap-2 mt-1">
        {run.subkind && (
          <span className="text-[10px] text-text-muted font-mono truncate">
            {run.subkind}
          </span>
        )}
        {run.current_rating != null && (
          <span className="text-[10px] text-text-muted ml-auto">
            ★ {run.current_rating}/5
          </span>
        )}
        {run.rateable && run.current_rating == null && (
          <span className="text-[10px] text-amber-400 ml-auto">unrated</span>
        )}
      </div>
    </div>
  )
}

// ── Detail sidebar ───────────────────────────────────────────────

function RunDetailSidebar({
  detail,
  activeNodeId,
  onRated,
}: {
  detail: RunDetailResp
  activeNodeId: string | null
  onRated: () => void
}) {
  // Find the active node's data (if any)
  const activeNode = activeNodeId
    ? detail.nodes.find((n) => n.id === activeNodeId)
    : null
  return (
    <div className="p-3 text-xs space-y-3">
      {/* Active node detail */}
      {activeNode && (
        <div className="p-2 bg-surface-tertiary rounded">
          <div className="text-text-muted">Selected node</div>
          <div className="font-semibold text-text-primary mt-1">
            {activeNode.label}
          </div>
          {activeNode.sublabel && (
            <div className="text-text-secondary mt-1 break-words">
              {activeNode.sublabel}
            </div>
          )}
          <div className="text-[10px] text-text-muted mt-1 uppercase">
            kind: {activeNode.kind}
          </div>
        </div>
      )}

      {/* Tool-call breakdown (if present) */}
      {detail.tool_calls && detail.tool_calls.length > 0 && (
        <details className="bg-surface-tertiary rounded">
          <summary className="cursor-pointer px-2 py-1 text-text-secondary font-semibold">
            Tool calls ({detail.tool_calls.length})
          </summary>
          <div className="p-2 space-y-1">
            {detail.tool_calls.map((tc: any, i: number) => (
              <div key={i} className="text-text-secondary">
                <span className="font-mono text-accent">{tc.name}</span>
                <span className="text-text-muted ml-2">
                  iter={tc.iter} · {tc.result_chars}b
                </span>
                {tc.blocked && (
                  <span className="text-red-400 ml-2">BLOCKED</span>
                )}
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Full prompt */}
      {detail.full_prompt && (
        <details open className="bg-surface-tertiary rounded">
          <summary className="cursor-pointer px-2 py-1 text-text-secondary font-semibold">
            Full prompt ({detail.full_prompt.length.toLocaleString()} chars)
          </summary>
          <pre className="p-2 text-[10px] text-text-secondary whitespace-pre-wrap break-words max-h-96 overflow-y-auto">
            {detail.full_prompt}
          </pre>
        </details>
      )}

      {/* Full output */}
      {detail.full_output && (
        <details open className="bg-surface-tertiary rounded">
          <summary className="cursor-pointer px-2 py-1 text-text-secondary font-semibold">
            Full output ({detail.full_output.length.toLocaleString()} chars)
          </summary>
          <pre className="p-2 text-[10px] text-text-secondary whitespace-pre-wrap break-words max-h-96 overflow-y-auto">
            {detail.full_output}
          </pre>
        </details>
      )}

      {/* Rating UI */}
      {detail.rateable && detail.sibling_task_id != null && (
        <RatingWidget
          siblingTaskId={detail.sibling_task_id}
          currentRating={detail.current_rating ?? null}
          currentNotes={detail.current_notes ?? ''}
          onRated={onRated}
        />
      )}
    </div>
  )
}

function RatingWidget({
  siblingTaskId,
  currentRating,
  currentNotes,
  onRated,
}: {
  siblingTaskId: number
  currentRating: number | null
  currentNotes: string
  onRated: () => void
}) {
  const [rating, setRating] = useState<number>(currentRating ?? 0)
  const [notes, setNotes] = useState(currentNotes)
  const [datasetCandidate, setDatasetCandidate] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    if (rating < 1) {
      setErr('Pick a rating 1-5')
      return
    }
    setSubmitting(true)
    setErr(null)
    try {
      await apiFetch('/overseer/runs/rate', {
        method: 'POST',
        body: JSON.stringify({
          sibling_task_id: siblingTaskId,
          rating,
          notes,
          dataset_candidate: datasetCandidate,
        }),
      })
      setDone(true)
      onRated()
    } catch (e: any) {
      setErr(e?.message || String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="p-2 bg-surface-tertiary rounded space-y-2">
      <div className="text-text-secondary font-semibold">
        Rate this run {currentRating != null && '(already rated)'}
      </div>
      <div className="flex gap-1">
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            onClick={() => setRating(n)}
            className={`w-8 h-8 rounded font-bold text-sm transition-colors ${
              rating >= n
                ? 'bg-amber-400 text-slate-900'
                : 'bg-surface-secondary text-text-muted hover:bg-surface-secondary/70'
            }`}
          >
            ★
          </button>
        ))}
      </div>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Notes (cite pre-commit, be honest about restatement vs lift)"
        rows={3}
        className="w-full bg-surface-secondary text-text-primary text-xs p-2 rounded"
      />
      <label className="flex items-center gap-2 cursor-pointer text-text-secondary">
        <input
          type="checkbox"
          checked={datasetCandidate}
          onChange={(e) => setDatasetCandidate(e.target.checked)}
        />
        <span className="text-[11px]">
          Flag as dataset_candidate (for future C training)
        </span>
      </label>
      <button
        onClick={submit}
        disabled={submitting || done}
        className="w-full px-3 py-1.5 bg-accent hover:bg-accent-hover text-white text-xs rounded disabled:opacity-50"
      >
        {done ? '✓ Rated' : submitting ? 'Submitting…' : 'Submit rating'}
      </button>
      {err && (
        <div className="text-red-400 text-[11px]">{err}</div>
      )}
    </div>
  )
}
