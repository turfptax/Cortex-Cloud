import { useEffect, useState } from 'react'
import { apiFetch } from '../../../lib/api'

// ── Squeeze: the AI report card (IA overhaul 2026-07-10) ──────────
// Replaces the sunset Dialectic tab. Renders the graded-dispatch data
// the Lemon Squeezer pipeline exports: every B-agent / sibling /
// Claude Code run Tory rated 1-5, aggregated per model and per task.
// Data comes straight from the Pi (source of truth); Lemon holds a
// copy for cross-tool routing reports. Grading new runs happens in
// System > Activity; this page is the scoreboard.

interface DispatchRow {
  dispatch_id: string
  task_type: string
  model: string
  rating: number
  cost_usd: number | null
  latency_ms: number | null
  completed_at?: string | null
}

interface ExportResp {
  ok?: boolean
  dispatches?: DispatchRow[]
  rows?: DispatchRow[]
}

/** Merge provider-prefixed and bare model ids ("anthropic/claude-x"
 * and "claude-x" are the same model in this data). */
function normModel(id: string): string {
  return (id || 'unknown').split('/').pop() || 'unknown'
}

interface Agg {
  key: string
  n: number
  avg: number
  passRate: number
  cost: number
}

function aggregate(rows: DispatchRow[], keyOf: (r: DispatchRow) => string): Agg[] {
  const groups = new Map<string, DispatchRow[]>()
  for (const r of rows) {
    const k = keyOf(r)
    const g = groups.get(k)
    if (g) g.push(r)
    else groups.set(k, [r])
  }
  return [...groups.entries()]
    .map(([key, g]) => ({
      key,
      n: g.length,
      avg: g.reduce((s, r) => s + r.rating, 0) / g.length,
      passRate: g.filter((r) => r.rating >= 4).length / g.length,
      cost: g.reduce((s, r) => s + (r.cost_usd || 0), 0),
    }))
    .sort((a, b) => b.n - a.n || b.avg - a.avg)
}

// Squeeze v2 (2026-07-11, Tory's ask): conversations are data points
// too. The interaction_feedback layer (Hub chat thumbs + phone voice
// chat / Bell ratings, with notes) joins the report card alongside
// the graded dispatches.
interface FeedbackSummary {
  ok: boolean
  totals?: { count: number; up: number; down: number; with_notes: number }
  by_kind?: Record<string, { up: number; down: number; n: number }>
  by_model?: Record<string, { up: number; down: number; n: number }>
  recent?: {
    id: number
    target_kind: string
    rating: number
    note: string
    model: string
    source: string
    created_at: string
  }[]
}

const KIND_LABEL: Record<string, string> = {
  chat_turn: 'Hub chat',
  chat_thread: 'Hub thread',
  voice_chat: 'Phone voice',
  bell_notification: 'Bell',
  dispatch: 'Dispatch',
  screen: 'Screen',
}

export function SqueezePanel() {
  const [rows, setRows] = useState<DispatchRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [fb, setFb] = useState<FeedbackSummary | null>(null)

  useEffect(() => {
    apiFetch<ExportResp>('/overseer/dispatch-export?limit=1000')
      .then((r) => setRows(r.dispatches || r.rows || []))
      .catch((e) => setError(e?.message || String(e)))
      .finally(() => setLoading(false))
    apiFetch<FeedbackSummary>('/overseer/feedback/summary')
      .then((r) => { if (r.ok) setFb(r) })
      .catch(() => { /* feedback section just stays hidden */ })
  }, [])

  const byModel = aggregate(rows, (r) => normModel(r.model))
  const byTask = aggregate(rows, (r) => r.task_type || '(untyped)')
  const hist = [1, 2, 3, 4, 5].map(
    (v) => rows.filter((r) => r.rating === v).length,
  )
  const histMax = Math.max(1, ...hist)
  const newest = rows.reduce<string>(
    (m, r) => (r.completed_at && r.completed_at > m ? r.completed_at : m),
    '',
  )
  const stale =
    newest &&
    Date.now() - new Date(newest.replace(' ', 'T')).getTime() >
      14 * 86_400_000

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-text-primary">
          The AI report card
        </h3>
        <p className="text-xs text-text-muted mt-1">
          Every dispatched AI run you graded (B agents, siblings, Claude
          Code), scored 1-5. Grade new runs in System &gt; Activity; these
          numbers also feed Lemon Squeeze for cross-tool model routing.
        </p>
        {stale && (
          <p className="text-xs text-text-muted mt-1">
            No newly graded dispatches since {newest.slice(0, 10)} — the
            scoreboard grows when new runs are dispatched and rated.
          </p>
        )}
      </div>

      {loading && <div className="text-xs text-text-muted">Loading…</div>}
      {error && <div className="text-xs text-red-400">{error}</div>}
      {!loading && !error && rows.length === 0 && (
        <div className="text-xs text-text-muted">
          No graded dispatches yet.
        </div>
      )}

      {/* Squeeze v2: the conversations report card. Thumbs + notes from
          Hub chat, phone voice chats, and Bell interactions. */}
      {fb?.totals && fb.totals.count > 0 && (
        <div className="bg-surface-secondary rounded-lg p-4 space-y-4">
          <div>
            <h4 className="text-xs font-semibold text-text-primary">
              Conversations &amp; feedback
            </h4>
            <p className="text-xs text-text-muted mt-1">
              Your thumbs + notes on live interactions (Hub chat, phone
              voice, Bell) — {fb.totals.count} total: 👍 {fb.totals.up} ·
              👎 {fb.totals.down} · {fb.totals.with_notes} with notes.
            </p>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
                By surface
              </div>
              <table className="w-full text-xs">
                <tbody>
                  {Object.entries(fb.by_kind || {}).map(([k, v]) => (
                    <tr key={k} className="border-t border-border/40">
                      <td className="py-1 pr-3">{KIND_LABEL[k] || k}</td>
                      <td className="py-1 pr-3 text-emerald-300">👍 {v.up}</td>
                      <td className="py-1 pr-3 text-red-300">👎 {v.down}</td>
                      <td className="py-1 text-text-muted">{v.n} total</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {Object.keys(fb.by_model || {}).length > 0 && (
              <div>
                <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
                  By model
                </div>
                <table className="w-full text-xs">
                  <tbody>
                    {Object.entries(fb.by_model || {})
                      .sort((a, b) => b[1].n - a[1].n)
                      .map(([k, v]) => (
                        <tr key={k} className="border-t border-border/40">
                          <td className="py-1 pr-3 font-mono">{normModel(k)}</td>
                          <td className="py-1 pr-3 text-emerald-300">👍 {v.up}</td>
                          <td className="py-1 pr-3 text-red-300">👎 {v.down}</td>
                          <td className="py-1 text-text-muted">{v.n}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {(fb.recent || []).some((r) => r.note) && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-text-muted mb-1">
                Recent notes
              </div>
              <div className="space-y-1.5">
                {(fb.recent || [])
                  .filter((r) => r.note)
                  .slice(0, 8)
                  .map((r) => (
                    <div
                      key={r.id}
                      className="flex items-start gap-2 text-xs rounded border border-border/40 bg-surface-tertiary/40 px-2 py-1.5"
                    >
                      <span>{r.rating > 0 ? '👍' : r.rating < 0 ? '👎' : '✎'}</span>
                      <span className="flex-1 text-text-primary">{r.note}</span>
                      <span className="text-text-muted shrink-0">
                        {KIND_LABEL[r.target_kind] || r.target_kind}
                        {r.model ? ` · ${normModel(r.model)}` : ''}
                      </span>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}

      {rows.length > 0 && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <AggTable title="By model" label="Model" aggs={byModel} />
            <AggTable title="By task" label="Task" aggs={byTask} />
          </div>

          <div className="bg-surface-secondary rounded-lg p-4">
            <h4 className="text-xs font-semibold text-text-primary mb-3">
              Rating distribution ({rows.length} graded)
            </h4>
            <div className="flex items-end gap-3 h-24">
              {hist.map((n, i) => (
                <div key={i} className="flex flex-col items-center flex-1">
                  <span className="text-xs text-text-muted mb-1">{n}</span>
                  <div
                    className="w-full rounded-t bg-accent/70"
                    style={{ height: `${(n / histMax) * 100}%` }}
                  />
                  <span className="text-xs text-text-muted mt-1">
                    {i + 1}★
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-surface-secondary rounded-lg p-4 overflow-x-auto">
            <h4 className="text-xs font-semibold text-text-primary mb-2">
              Dispatches
            </h4>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-muted text-left">
                  <th className="py-1 pr-3">#</th>
                  <th className="py-1 pr-3">Task</th>
                  <th className="py-1 pr-3">Model</th>
                  <th className="py-1 pr-3">Rating</th>
                  <th className="py-1 pr-3">Cost</th>
                  <th className="py-1">Latency</th>
                </tr>
              </thead>
              <tbody>
                {[...rows].reverse().map((r) => (
                  <tr
                    key={r.dispatch_id}
                    className="border-t border-border text-text-secondary"
                  >
                    <td className="py-1 pr-3 text-text-muted">
                      {r.dispatch_id}
                    </td>
                    <td className="py-1 pr-3">{r.task_type}</td>
                    <td className="py-1 pr-3">{normModel(r.model)}</td>
                    <td className="py-1 pr-3">
                      <span
                        className={
                          r.rating >= 4
                            ? 'text-success'
                            : r.rating <= 2
                              ? 'text-red-400'
                              : 'text-text-primary'
                        }
                      >
                        {'★'.repeat(r.rating)}
                        <span className="text-text-muted">
                          {'★'.repeat(5 - r.rating)}
                        </span>
                      </span>
                    </td>
                    <td className="py-1 pr-3">
                      {r.cost_usd != null ? `$${r.cost_usd.toFixed(3)}` : '—'}
                    </td>
                    <td className="py-1">
                      {r.latency_ms != null && r.latency_ms < 3_600_000
                        ? `${Math.round(r.latency_ms / 1000)}s`
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

function AggTable({
  title,
  label,
  aggs,
}: {
  title: string
  label: string
  aggs: Agg[]
}) {
  return (
    <div className="bg-surface-secondary rounded-lg p-4">
      <h4 className="text-xs font-semibold text-text-primary mb-2">{title}</h4>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-muted text-left">
            <th className="py-1 pr-3">{label}</th>
            <th className="py-1 pr-3">Runs</th>
            <th className="py-1 pr-3">Avg</th>
            <th className="py-1 pr-3">Pass ≥4</th>
            <th className="py-1">Cost</th>
          </tr>
        </thead>
        <tbody>
          {aggs.map((a) => (
            <tr key={a.key} className="border-t border-border text-text-secondary">
              <td className="py-1 pr-3 font-medium text-text-primary">
                {a.key}
              </td>
              <td className="py-1 pr-3">{a.n}</td>
              <td className="py-1 pr-3">{a.avg.toFixed(2)}</td>
              <td className="py-1 pr-3">{Math.round(a.passRate * 100)}%</td>
              <td className="py-1">${a.cost.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
