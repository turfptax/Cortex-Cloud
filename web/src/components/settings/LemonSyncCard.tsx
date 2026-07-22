import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../../lib/api'

interface LemonStats {
  runs: number
  ok_runs: number
  success_rate: number | null
  total_sent: number
  total_persisted: number
  last_sync: { ts?: string; ok?: boolean; sent?: number; error?: string } | null
}

export interface LemonStatus {
  enabled: boolean
  running: boolean
  reachable: boolean
  lemon_url: string
  interval_s: number
  cursor: number
  stats: LemonStats
}

/** Settings integration card: a live on/off toggle for the Lemon Squeezer
 * dispatch sync, plus a health dot, headline numbers, and a manual trigger.
 * The fuller reporting (history table) lives in System > Lemon Sync. */
export function LemonSyncCard() {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)

  const status = useQuery({
    queryKey: ['lemon-status'],
    queryFn: () => apiFetch<LemonStatus>('/lemon/status'),
    refetchInterval: 20_000,
  })
  const s = status.data

  const toggle = async () => {
    if (!s || busy) return
    setBusy(true)
    try {
      await apiFetch('/lemon/enable', {
        method: 'POST',
        body: JSON.stringify({ enabled: !s.enabled }),
      })
      await qc.invalidateQueries({ queryKey: ['lemon-status'] })
    } finally {
      setBusy(false)
    }
  }

  const syncNow = async () => {
    if (busy) return
    setBusy(true)
    setSyncMsg(null)
    try {
      const r = await apiFetch<{ ok: boolean; sent?: number; note?: string; error?: string }>(
        '/lemon/export',
        { method: 'POST' }
      )
      setSyncMsg(
        r.ok ? (r.sent ? `Synced ${r.sent}` : r.note || 'Nothing new') : `Failed: ${r.error}`
      )
      await qc.invalidateQueries({ queryKey: ['lemon-status'] })
    } catch {
      setSyncMsg('Sync failed')
    } finally {
      setBusy(false)
    }
  }

  const dotClass = !s || !s.enabled
    ? 'bg-surface-tertiary'
    : s.reachable
      ? 'bg-success'
      : 'bg-warning'
  const dotLabel = !s
    ? '…'
    : !s.enabled
      ? 'Disabled'
      : s.reachable
        ? 'Connected'
        : 'Lemon unreachable'

  return (
    <div className="bg-surface rounded-xl border border-border p-6">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold text-text-primary flex items-center gap-2">
          <span>🍋</span> Lemon Squeezer Sync
        </h2>
        <div className="flex items-center gap-2 text-xs text-text-muted">
          <span className={`w-2 h-2 rounded-full ${dotClass}`} />
          {dotLabel}
        </div>
      </div>

      <p className="text-xs text-text-muted mb-4">
        Ships graded sub-agent dispatch ratings to Lemon Squeezer to train its
        model router. Metadata only — no prompt or response text leaves Cortex.
      </p>

      {/* Toggle */}
      <label className="flex items-center gap-3 cursor-pointer mb-4">
        <div
          onClick={toggle}
          className={`w-10 h-5 rounded-full transition-colors relative cursor-pointer ${
            s?.enabled ? 'bg-accent' : 'bg-surface-tertiary'
          } ${busy ? 'opacity-50' : ''}`}
        >
          <div
            className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
              s?.enabled ? 'translate-x-5' : 'translate-x-0.5'
            }`}
          />
        </div>
        <div>
          <span className="text-sm text-text-primary">Enable sync</span>
          <p className="text-xs text-text-muted">
            Polls every {s ? Math.round(s.interval_s / 60) : 15} min
            {s?.lemon_url ? ` · ${s.lemon_url}` : ''}
          </p>
        </div>
      </label>

      {/* Headline numbers */}
      <div className="flex items-center gap-5 text-xs text-text-muted mb-4">
        <span>
          Synced <b className="text-text-primary">{s?.stats.total_persisted ?? 0}</b>
        </span>
        <span>
          Success <b className="text-text-primary">{fmtPct(s?.stats.success_rate)}</b>
        </span>
        <span>
          Cursor <b className="text-text-primary">{s?.cursor ?? 0}</b>
        </span>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={syncNow}
          disabled={busy}
          className="px-3 py-1.5 bg-surface-secondary border border-border text-sm rounded-lg hover:border-accent transition-colors disabled:opacity-50 cursor-pointer"
        >
          Sync now
        </button>
        {syncMsg && <span className="text-xs text-text-muted">{syncMsg}</span>}
      </div>
    </div>
  )
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${Math.round(v * 100)}%`
}
