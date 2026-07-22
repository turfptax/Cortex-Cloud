import { useEffect, useState } from 'react'
import {
  listSessions,
  STATUS_DISPLAY,
  type SessionListEntry,
} from '../../lib/videoApi'

/** History view — recent sessions across all modes.
 *
 * Backed by GET /api/video/sessions. Phase 1 just lists them; clicking
 * a row to expand into the full session view will land in a later
 * checkpoint when there's enough history to make it worthwhile. */
export function SessionList() {
  const [sessions, setSessions] = useState<SessionListEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const refresh = async () => {
      try {
        const list = await listSessions({ limit: 50 })
        if (!cancelled) {
          setSessions(list)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    refresh()
    const id = setInterval(refresh, 5_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  return (
    <div className="max-w-4xl mx-auto p-6">
      <div className="bg-surface rounded-xl border border-border">
        <div className="p-4 border-b border-border">
          <h3 className="text-sm font-semibold text-text-primary">
            Recent sessions
          </h3>
          <p className="text-xs text-text-muted mt-0.5">
            Most recent first. Refreshes every 5s.
          </p>
        </div>

        {loading && sessions.length === 0 && (
          <p className="p-4 text-sm text-text-muted">Loading…</p>
        )}
        {error && (
          <p className="p-4 text-sm text-error">Could not load: {error}</p>
        )}
        {!loading && sessions.length === 0 && !error && (
          <p className="p-4 text-sm text-text-muted italic">
            No sessions yet. Submit a URL on the File tab to get started.
          </p>
        )}

        <ul className="divide-y divide-border">
          {sessions.map((s) => (
            <li
              key={s.id}
              className="p-3 flex items-center justify-between gap-3 hover:bg-surface-secondary transition-colors"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span
                    className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${STATUS_DISPLAY[s.status].color}`}
                  >
                    {STATUS_DISPLAY[s.status].label}
                  </span>
                  <span className="text-xs text-text-muted uppercase tracking-wide">
                    {s.mode}
                  </span>
                  <span className="text-xs font-mono text-text-muted">
                    {s.id.slice(0, 8)}
                  </span>
                </div>
                <div className="text-xs text-text-muted mt-0.5">
                  {fmtDate(s.started_at)}
                  {s.duration_s != null && ` · ${s.duration_s.toFixed(1)}s`}
                  {s.project_id && ` · ${s.project_id}`}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

function fmtDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}
