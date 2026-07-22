import { useState } from 'react'
import { fmtTime } from '../../../lib/time'
import {
  type NotificationAction,
  type NotificationRow,
} from '../shared'

export function NotificationsPanel({
  notifications,
  onDismiss,
  onDismissAll,
  onOpenInChat,
  onAction,
  onRespond,
}: {
  notifications: NotificationRow[]
  onDismiss: (id: number) => void
  onDismissAll: () => void
  onOpenInChat: (n: NotificationRow) => void
  onAction: (
    id: number,
    action: 'archive' | 'snooze' | 'touch',
    snooze_days?: number,
  ) => void
  // Slice 9.6 CP1: respond to a custom action button. Distinct from
  // onAction (which targets the built-in archive/snooze/touch flow).
  onRespond: (
    notification_id: number,
    action_kind: string,
    action_label: string,
    response_payload?: Record<string, any>,
    also_archive?: boolean,
  ) => void
}) {
  const unread = notifications.filter(
    (n) => !n.dismissed_at && !n.archived_at,
  )

  // Polish CP2: group by rule_name. Single-row groups (e.g. one
  // import_backlog notification) render flat; multi-row groups
  // collapse into a summary header you can expand.
  const groups = unread.reduce<Map<string, NotificationRow[]>>(
    (acc, n) => {
      const k = n.rule_name || 'unknown'
      const list = acc.get(k) || []
      list.push(n)
      acc.set(k, list)
      return acc
    },
    new Map(),
  )
  // Sort groups: most-severe-then-largest first, so 'important' rules
  // and big stale clusters surface above noise.
  const sortedGroups = Array.from(groups.entries()).sort((a, b) => {
    const sevRank = (g: NotificationRow[]) => {
      if (g.some((n) => n.severity === 'important')) return 2
      if (g.some((n) => n.severity === 'warn')) return 1
      return 0
    }
    return sevRank(b[1]) - sevRank(a[1]) || b[1].length - a[1].length
  })

  // Per-group expanded state.
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set())
  const toggle = (k: string) => {
    setOpenGroups((prev) => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  }

  const handleArchiveGroup = (rows: NotificationRow[]) => {
    rows.forEach((n) => onAction(n.id, 'archive'))
  }
  const handleSnoozeGroup = (rows: NotificationRow[]) => {
    rows.forEach((n) => onAction(n.id, 'snooze', 30))
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-text-primary">
            Notifications ({unread.length} unread, {groups.size} rule
            {groups.size === 1 ? '' : 's'})
          </h3>
          {unread.length > 0 && (
            <button
              onClick={onDismissAll}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer"
            >
              Dismiss all
            </button>
          )}
        </div>
        {unread.length === 0 ? (
          <div className="text-sm text-text-muted py-8 text-center">
            All caught up. Notifications will appear here when the overseer
            flags something — stale projects, automation anomalies, growing
            backlogs.
          </div>
        ) : (
          <ul className="space-y-2">
            {sortedGroups.map(([rule_name, rows]) => {
              const isOpen = openGroups.has(rule_name)
              const isSingle = rows.length === 1
              // Severity for the group = highest severity of any row.
              const sev = rows.some((n) => n.severity === 'important')
                ? 'important'
                : rows.some((n) => n.severity === 'warn')
                  ? 'warn'
                  : 'info'
              const sevClass =
                sev === 'important'
                  ? 'bg-red-500/20 text-red-400'
                  : sev === 'warn'
                    ? 'bg-amber-500/20 text-amber-400'
                    : 'bg-text-muted/20 text-text-muted'
              return (
                <li
                  key={rule_name}
                  className="rounded-lg border border-border bg-surface-secondary"
                >
                  {/* Group header — always shown */}
                  <div className="p-3 flex items-baseline gap-2 flex-wrap">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${sevClass}`}
                    >
                      {sev}
                    </span>
                    <span className="text-sm text-text-primary font-medium font-mono">
                      {rule_name}
                    </span>
                    <span className="text-xs text-text-muted">
                      ({rows.length} {rows.length === 1 ? 'row' : 'rows'})
                    </span>
                    {!isSingle && (
                      <button
                        onClick={() => toggle(rule_name)}
                        className="text-[10px] uppercase tracking-wide text-text-muted hover:text-text-primary cursor-pointer ml-1"
                      >
                        {isOpen ? '▾ collapse' : '▸ expand'}
                      </button>
                    )}
                    <div className="ml-auto flex items-center gap-1 flex-wrap">
                      {!isSingle && (
                        <>
                          <button
                            onClick={() => handleArchiveGroup(rows)}
                            className="px-2 py-0.5 rounded text-[10px] font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-secondary cursor-pointer"
                            title={`Archive all ${rows.length} ${rule_name} notifications`}
                          >
                            Archive all
                          </button>
                          <button
                            onClick={() => handleSnoozeGroup(rows)}
                            className="px-2 py-0.5 rounded text-[10px] font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-secondary cursor-pointer"
                            title={`Snooze all ${rows.length} for 30 days`}
                          >
                            Snooze all 30d
                          </button>
                        </>
                      )}
                    </div>
                  </div>

                  {/* For single-row groups, render the single notification
                      inline (no separate expand-to-see). For multi-row,
                      show body when expanded. */}
                  {(isSingle || isOpen) && (
                    <ul className="border-t border-border divide-y divide-border">
                      {rows.map((n) => (
                        <li key={n.id} className="p-3 flex justify-between gap-3">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-baseline gap-2">
                              <span className="text-sm text-text-primary font-medium">
                                {n.title}
                              </span>
                              <span className="text-xs text-text-muted ml-auto whitespace-nowrap" title={fmtTime((n as any).local_created_at, n.created_at)}>
                                {fmtTime((n as any).local_created_at, n.created_at)}
                              </span>
                            </div>
                            {n.body && (
                              <p className="text-xs text-text-secondary mt-1.5 leading-relaxed">
                                {n.body}
                              </p>
                            )}
                            <div className="text-[10px] text-text-muted mt-1.5 font-mono">
                              key={n.rule_key}
                            </div>
                            {/* Slice 9.6 CP1 (2026-05-19): custom action
                                buttons attached by overseer when the
                                notification was emitted. Above the
                                standard Open/Archive/Snooze/Touch row
                                so they're the visually-primary CTA. */}
                            {n.actions && n.actions.length > 0 && (
                              <NotificationCustomActions
                                notification={n}
                                onRespond={onRespond}
                              />
                            )}
                            <div className="mt-2 flex items-center gap-2 flex-wrap">
                              <button
                                onClick={() => onOpenInChat(n)}
                                className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-accent/15 hover:bg-accent/25 text-accent-hover cursor-pointer"
                                title="Pre-fills the Chat tab with this notification's context"
                              >
                                Open in chat
                              </button>
                              <button
                                onClick={() => onAction(n.id, 'archive')}
                                className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-secondary cursor-pointer"
                                title="Acknowledge and hide permanently"
                              >
                                Archive
                              </button>
                              <button
                                onClick={() => onAction(n.id, 'snooze', 30)}
                                className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-secondary cursor-pointer"
                                title="Hide for 30 days"
                              >
                                Snooze 30d
                              </button>
                              <button
                                onClick={() => onAction(n.id, 'touch')}
                                className="px-2.5 py-1 rounded-md text-[11px] font-medium text-text-muted hover:text-text-primary cursor-pointer"
                                title="Pull back to actionable queue"
                              >
                                Touch
                              </button>
                            </div>
                          </div>
                          <button
                            onClick={() => onDismiss(n.id)}
                            className="text-text-muted hover:text-text-primary text-lg leading-none cursor-pointer self-start"
                            title="Dismiss (light: 'noted, move on')"
                          >
                            ✕
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}


export function NotificationCustomActions({
  notification,
  onRespond,
}: {
  notification: NotificationRow
  onRespond: (
    notification_id: number,
    action_kind: string,
    action_label: string,
    response_payload?: Record<string, any>,
    also_archive?: boolean,
  ) => void
}) {
  const [freeTextOpen, setFreeTextOpen] = useState<string | null>(null)
  const [freeTextValue, setFreeTextValue] = useState('')
  const actions = notification.actions || []
  if (actions.length === 0) return null

  const click = (a: NotificationAction) => {
    if (a.kind === 'free_text') {
      setFreeTextOpen(a.label)
      setFreeTextValue('')
      return
    }
    if (a.kind === 'yes_no') {
      // The action's payload.value should carry 'yes' or 'no'; if not,
      // derive from the label as a best-effort fallback.
      const value =
        a.payload?.value ??
        (a.label.toLowerCase().includes('yes') ? 'yes' : 'no')
      onRespond(notification.id, a.kind, a.label, { ...a.payload, value }, true)
      return
    }
    // dispatch_sibling + predefined CRUD + custom — fire immediately
    onRespond(
      notification.id,
      a.kind,
      a.label,
      a.payload || {},
      true,  // auto-archive — Tory has handled the notification
    )
  }

  const submitFreeText = (label: string) => {
    if (!freeTextValue.trim()) return
    onRespond(
      notification.id,
      'free_text',
      label,
      { value: freeTextValue.trim() },
      true,
    )
    setFreeTextOpen(null)
    setFreeTextValue('')
  }

  return (
    <div className="mt-2.5 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        {actions.map((a, i) => {
          // Color cue per kind so Tory eyes-down can tell them apart.
          const cls =
            a.kind === 'yes_no'
              ? 'bg-accent/20 hover:bg-accent/30 text-accent-hover border-accent/40'
              : a.kind === 'free_text'
                ? 'bg-surface-tertiary hover:bg-surface-tertiary/80 text-text-primary border-border'
                : a.kind === 'dispatch_sibling'
                  ? 'bg-purple-500/15 hover:bg-purple-500/25 text-purple-300 border-purple-500/30'
                  : 'bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border-amber-500/30'
          return (
            <button
              key={i}
              onClick={() => click(a)}
              className={`px-2.5 py-1 rounded-md text-[11px] font-medium border cursor-pointer ${cls}`}
              title={`kind: ${a.kind}${a.payload ? ' · payload: ' + JSON.stringify(a.payload).slice(0, 80) : ''}`}
            >
              {a.label}
            </button>
          )
        })}
      </div>
      {freeTextOpen !== null && (
        <div className="rounded-md border border-border bg-surface-tertiary/40 p-2">
          <div className="text-[10px] text-text-muted mb-1">
            Reply to overseer — your text is logged and surfaced to them
            on their next tick.
          </div>
          <textarea
            value={freeTextValue}
            onChange={(e) => setFreeTextValue(e.target.value)}
            rows={3}
            autoFocus
            placeholder="Type your reply…"
            className="w-full text-xs rounded border border-border bg-surface-secondary p-2 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent resize-none"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                submitFreeText(freeTextOpen)
              }
            }}
          />
          <div className="mt-1.5 flex items-center gap-2 justify-end">
            <button
              onClick={() => {
                setFreeTextOpen(null)
                setFreeTextValue('')
              }}
              className="px-2.5 py-1 rounded-md text-[11px] font-medium text-text-muted hover:text-text-primary cursor-pointer"
            >
              Cancel
            </button>
            <button
              onClick={() => submitFreeText(freeTextOpen)}
              disabled={!freeTextValue.trim()}
              className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-accent/20 hover:bg-accent/30 text-accent-hover border border-accent/40 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Send reply (Ctrl+Enter)
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

