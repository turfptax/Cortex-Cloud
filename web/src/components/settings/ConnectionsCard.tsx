import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../../lib/api'

type ConnectionStatus = 'pending' | 'active' | 'revoked'
type ConnectionLevel = 'none' | 'full'

interface Connection {
  id: string
  client_id: string
  name: string | null
  redirect_host: string | null
  level: ConnectionLevel
  approval_policy: string | null
  status: ConnectionStatus
  first_connected_at: string | null
  last_connected_at: string | null
  last_used_at: string | null
  granted_at: string | null
  token_status: string | null
}

function levelLabel(level: ConnectionLevel): string {
  return level === 'full' ? 'Full corpus (read + write)' : 'Metadata only'
}

function statusRank(status: ConnectionStatus): number {
  // Pending first, then active. Revoked is filtered out before sorting.
  return status === 'pending' ? 0 : 1
}

function StatusBadge({ status }: { status: ConnectionStatus }) {
  const cls: Record<ConnectionStatus, string> = {
    pending: 'bg-warning/15 text-warning',
    active: 'bg-success/15 text-success',
    revoked: 'bg-surface-tertiary text-text-muted',
  }
  const label: Record<ConnectionStatus, string> = {
    pending: 'Pending',
    active: 'Active',
    revoked: 'Revoked',
  }
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls[status]}`}>
      {label[status]}
    </span>
  )
}

/** Cloud Settings card: owner-gated connector approval.
 *
 * External AIs that reach Cortex over MCP land in a "pending" state with no
 * access. This card lists them and lets the owner approve (grant full read +
 * write) or revoke from the web, mirroring the phone's Connections screen.
 * Backed by the gateway's owner-gated /api/connections facade. */
export function ConnectionsCard() {
  const [connections, setConnections] = useState<Connection[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const res = await apiFetch<{ connections: Connection[] }>('/connections')
      setConnections(res.connections ?? [])
    } catch {
      setError('Could not load connections')
      if (connections === null) setConnections([])
    }
  }, [connections])

  useEffect(() => {
    load()
    // Run once on mount; load reads connections only to gate the error fallback.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const approve = async (id: string) => {
    setBusyId(id)
    setError(null)
    try {
      await apiFetch(`/connections/${id}/approve`, {
        method: 'POST',
        body: JSON.stringify({ level: 'full', always: true }),
      })
      await load()
    } catch {
      setError('Approve failed')
    } finally {
      setBusyId(null)
    }
  }

  const revoke = async (id: string) => {
    if (
      !window.confirm(
        'Revoke this connection? The AI will lose all access until you approve it again.'
      )
    ) {
      return
    }
    setBusyId(id)
    setError(null)
    try {
      await apiFetch(`/connections/${id}/revoke`, { method: 'POST' })
      await load()
    } catch {
      setError('Revoke failed')
    } finally {
      setBusyId(null)
    }
  }

  const loading = connections === null
  const visible = (connections ?? [])
    .filter((c) => c.status !== 'revoked')
    .sort((a, b) => statusRank(a.status) - statusRank(b.status))

  return (
    <div className="bg-surface rounded-xl border border-border p-6">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-lg font-semibold text-text-primary flex items-center gap-2">
          <span>🔌</span> Connections
        </h2>
        <button
          onClick={load}
          disabled={busyId !== null}
          className="text-xs text-text-muted hover:text-text-primary transition-colors disabled:opacity-50 cursor-pointer"
        >
          Refresh
        </button>
      </div>
      <p className="text-xs text-text-muted mb-4">
        Approve the AI assistants that connect to your Cortex
      </p>

      <p className="text-sm text-text-secondary mb-4">
        External AIs (Claude, ChatGPT, Grok) that connect over MCP start with no
        access: they read and write nothing until you approve them here.
        Approving grants read AND write.
      </p>

      {error && (
        <div className="px-4 py-3 rounded-lg text-sm mb-4 bg-danger/10 border border-danger/30 text-danger">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-text-muted">Loading...</p>
      ) : visible.length === 0 ? (
        <p className="text-sm text-text-muted">No AI has connected yet</p>
      ) : (
        <div className="space-y-2">
          {visible.map((c) => (
            <div
              key={c.id}
              className="flex items-start justify-between gap-3 px-4 py-3 bg-surface-secondary border border-border rounded-lg"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm font-medium text-text-primary truncate">
                    {c.name || c.client_id}
                  </span>
                  <StatusBadge status={c.status} />
                </div>
                {c.redirect_host && (
                  <p className="text-xs text-text-muted truncate">{c.redirect_host}</p>
                )}
                <p className="text-xs text-text-secondary mt-0.5">
                  {levelLabel(c.level)}
                </p>
              </div>
              <div className="shrink-0">
                {c.status === 'pending' && (
                  <button
                    onClick={() => approve(c.id)}
                    disabled={busyId === c.id}
                    className="px-3 py-1.5 bg-accent text-white text-sm rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50 cursor-pointer"
                  >
                    {busyId === c.id ? 'Approving...' : 'Approve'}
                  </button>
                )}
                {c.status === 'active' && (
                  <button
                    onClick={() => revoke(c.id)}
                    disabled={busyId === c.id}
                    className="px-3 py-1.5 bg-surface-tertiary text-danger text-sm rounded-lg hover:bg-danger/15 transition-colors disabled:opacity-50 cursor-pointer"
                  >
                    {busyId === c.id ? 'Revoking...' : 'Revoke'}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
