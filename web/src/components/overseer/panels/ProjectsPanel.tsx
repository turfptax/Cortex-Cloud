import { fmtTime } from '../../../lib/time'
import {
  type ProjectClassRow,
} from '../shared'

// ── Slice 3i CP1: Projects classification table ─────────────

export function ProjectsPanel({
  projects,
  filter,
  setFilter,
  onSetClass,
  onClassifyNow,
  onRefresh,
  busy,
}: {
  projects: ProjectClassRow[]
  filter: string
  setFilter: (f: string) => void
  onSetClass: (
    project: string,
    treat_as: 'auto' | 'human' | 'automation' | 'ignore',
  ) => void
  onClassifyNow: () => void
  onRefresh: () => void
  busy: string
}) {
  const norm = filter.trim().toLowerCase()
  const filtered = norm
    ? projects.filter((p) =>
        (p.project || '(unclassified)').toLowerCase().includes(norm))
    : projects

  // Counts per classification — useful overview.
  const counts = projects.reduce<Record<string, number>>((acc, p) => {
    const k = p.treat_as || 'auto'
    acc[k] = (acc[k] || 0) + 1
    return acc
  }, {})

  const classBadge = (treat_as: string) => {
    if (treat_as === 'human') return 'bg-success/20 text-success'
    if (treat_as === 'automation') return 'bg-amber-500/20 text-amber-400'
    if (treat_as === 'ignore') return 'bg-text-muted/20 text-text-muted'
    return 'bg-accent/20 text-accent-hover'   // auto
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-6xl mx-auto space-y-4">
        <div className="flex items-baseline justify-between">
          <div>
            <h3 className="text-base font-semibold text-text-primary">
              Project classification
            </h3>
            <p className="text-xs text-text-muted mt-1">
              The auto-classifier tags each project as <em>human</em>,{' '}
              <em>automation</em>, or <em>auto</em> (unclassified) based on
              session count + median duration. Insight scans, rollups, and
              other loops respect this. Manual overrides are sticky — the
              auto-classifier won't touch them.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onRefresh}
              disabled={!!busy}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
            >
              Refresh
            </button>
            <button
              onClick={onClassifyNow}
              disabled={!!busy}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
            >
              {busy ? busy : 'Re-classify all'}
            </button>
          </div>
        </div>

        {/* Class summary pills */}
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          <span className="text-text-muted uppercase tracking-wide text-[10px]">
            Distribution:
          </span>
          {(['human', 'automation', 'auto', 'ignore'] as const).map((c) => (
            <span
              key={c}
              className={`px-2 py-0.5 rounded text-[10px] font-medium uppercase ${classBadge(c)}`}
            >
              {c} ({counts[c] || 0})
            </span>
          ))}
        </div>

        {/* Filter */}
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter projects…"
          className="w-full px-3 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded border border-border focus:outline-none focus:border-accent"
        />

        {/* Table */}
        {filtered.length === 0 ? (
          <div className="text-sm text-text-muted italic py-6 text-center">
            {projects.length === 0
              ? 'No projects yet — import some Claude sessions on the Overview tab.'
              : 'No projects match that filter.'}
          </div>
        ) : (
          <div className="border border-border rounded-lg overflow-x-auto">
            <table className="w-full text-xs min-w-[720px]">
              <thead className="bg-surface-secondary text-text-muted uppercase text-[10px]">
                <tr>
                  <th className="text-left px-3 py-2">Project</th>
                  <th className="text-right px-3 py-2">Sessions</th>
                  <th className="text-right px-3 py-2">Median min</th>
                  <th className="text-left px-3 py-2">Last seen</th>
                  <th className="text-left px-3 py-2">Class</th>
                  <th className="text-left px-3 py-2">Override → set</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((p, idx) => {
                  const display = p.project || '(unclassified)'
                  return (
                    <tr
                      key={p.project || `__unclassified_${idx}`}
                      className="border-t border-border hover:bg-surface-secondary/40"
                    >
                      <td className="px-3 py-2 text-text-primary font-medium truncate max-w-xs">
                        {display}
                      </td>
                      <td className="px-3 py-2 text-text-secondary text-right">
                        {p.session_count}
                      </td>
                      <td className="px-3 py-2 text-text-muted text-right">
                        {p.avg_duration_minutes?.toFixed(1) ?? '—'}
                      </td>
                      <td className="px-3 py-2 text-text-muted">
                        {p.last_seen?.slice(0, 10) || '—'}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase ${classBadge(p.treat_as)}`}
                        >
                          {p.treat_as}
                        </span>
                        {p.manual_override && (
                          <span
                            className="ml-1 text-[10px] text-text-muted"
                            title={`Set ${fmtTime((p as any).local_classified_at, p.classified_at) || ''} (${p.classified_reason || 'manual'})`}
                          >
                            🔒
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1 flex-wrap">
                          {(['human', 'automation', 'ignore', 'auto'] as const).map((c) => (
                            <button
                              key={c}
                              disabled={!!busy || p.treat_as === c}
                              onClick={() => onSetClass(p.project, c)}
                              className={`px-1.5 py-0.5 rounded text-[10px] uppercase font-medium cursor-pointer disabled:opacity-40 disabled:cursor-default ${
                                p.treat_as === c
                                  ? classBadge(c)
                                  : 'bg-surface-tertiary text-text-muted hover:text-text-primary'
                              }`}
                              title={c === 'auto'
                                ? 'Clear manual override; let the classifier decide'
                                : `Force this project to ${c}`}
                            >
                              {c}
                            </button>
                          ))}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

