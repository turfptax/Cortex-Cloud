import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../../lib/api'
import type { LemonStatus } from '../settings/LemonSyncCard'

interface SyncRecord {
  ts?: string
  ok?: boolean
  sent?: number
  attempted?: number
  persisted?: number
  duplicates?: number
  stage?: string
  error?: string
  cursor?: number
}

/** System > Lemon Sync: the reporting surface for the dispatch export.
 * This is performance telemetry about an egress process (model-routing
 * training data), so it lives under System, not Corpus. Control lives in
 * Settings > Lemon Squeezer Sync. */
export function LemonSyncPanel() {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)

  const status = useQuery({
    queryKey: ['lemon-status'],
    queryFn: () => apiFetch<LemonStatus>('/lemon/status'),
    refetchInterval: 20_000,
  })
  const history = useQuery({
    queryKey: ['lemon-history'],
    queryFn: () => apiFetch<{ history: SyncRecord[] }>('/lemon/history?limit=50'),
    refetchInterval: 20_000,
  })
  const s = status.data
  const rows = history.data?.history ?? []

  const syncNow = async () => {
    if (busy) return
    setBusy(true)
    try {
      await apiFetch('/lemon/export', { method: 'POST' })
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['lemon-status'] }),
        qc.invalidateQueries({ queryKey: ['lemon-history'] }),
      ])
    } finally {
      setBusy(false)
    }
  }

  const health = !s || !s.enabled
    ? { dot: 'bg-surface-tertiary', label: 'Disabled' }
    : s.reachable
      ? { dot: 'bg-success', label: s.running ? 'Syncing' : 'Enabled' }
      : { dot: 'bg-warning', label: 'Lemon unreachable' }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      {/* Header + status */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-text-primary flex items-center gap-2">
            <span>🍋</span> Lemon Sync
          </h2>
          <p className="text-xs text-text-muted mt-0.5">
            Graded dispatch export to Lemon Squeezer (model-router training data)
          </p>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-xs text-text-muted">
            <span className={`w-2 h-2 rounded-full ${health.dot}`} />
            {health.label}
          </div>
          <button
            onClick={syncNow}
            disabled={busy}
            className="px-3 py-1.5 bg-surface-secondary border border-border text-sm rounded-lg hover:border-accent transition-colors disabled:opacity-50 cursor-pointer"
          >
            Sync now
          </button>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="Dispatches synced" value={s?.stats.total_persisted ?? 0} />
        <Stat label="Success rate" value={fmtPct(s?.stats.success_rate)} />
        <Stat label="Sync runs" value={s?.stats.runs ?? 0} />
        <Stat label="Cursor" value={s?.cursor ?? 0} />
      </div>

      {/* Config line */}
      <div className="text-xs text-text-muted">
        Target <span className="text-text-secondary">{s?.lemon_url ?? '—'}</span>
        {' · '}every {s ? Math.round(s.interval_s / 60) : '—'} min
        {s?.stats.last_sync?.ts ? ` · last ${fmtTime(s.stats.last_sync.ts)}` : ''}
      </div>

      {/* History table */}
      <div className="bg-surface rounded-xl border border-border overflow-hidden">
        <div className="px-4 py-3 border-b border-border text-sm font-medium text-text-primary">
          Recent syncs
        </div>
        {rows.length === 0 ? (
          <p className="px-4 py-6 text-sm text-text-muted">
            No syncs recorded yet. Idle polls (nothing new) aren't logged — a row
            appears when dispatches ship or a push fails.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-text-muted border-b border-border">
                <th className="text-left font-medium px-4 py-2">When</th>
                <th className="text-left font-medium px-4 py-2">Result</th>
                <th className="text-right font-medium px-4 py-2">Sent</th>
                <th className="text-right font-medium px-4 py-2">Persisted</th>
                <th className="text-right font-medium px-4 py-2">Dups</th>
                <th className="text-right font-medium px-4 py-2">Cursor</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-b border-border/50 last:border-0">
                  <td className="px-4 py-2 text-text-secondary">{fmtTime(r.ts)}</td>
                  <td className="px-4 py-2">
                    {r.ok ? (
                      <span className="text-success">ok</span>
                    ) : (
                      <span className="text-danger" title={r.error || ''}>
                        {r.stage ? `${r.stage} failed` : 'failed'}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right text-text-secondary">{r.sent ?? r.attempted ?? 0}</td>
                  <td className="px-4 py-2 text-right text-text-secondary">{r.persisted ?? 0}</td>
                  <td className="px-4 py-2 text-right text-text-muted">{r.duplicates ?? 0}</td>
                  <td className="px-4 py-2 text-right text-text-muted">{r.cursor ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-surface rounded-xl border border-border p-4">
      <div className="text-2xl font-semibold text-text-primary">{value}</div>
      <div className="text-xs text-text-muted mt-1">{label}</div>
    </div>
  )
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${Math.round(v * 100)}%`
}

function fmtTime(ts?: string): string {
  if (!ts) return '—'
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ts
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
