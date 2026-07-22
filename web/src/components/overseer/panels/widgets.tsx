import { type ReactNode } from 'react'


// Suspense fallback for lazy-loaded panels (the graph tabs ship in
// their own chunk so the initial bundle stays lean).
export function PanelLoading({ label }: { label?: string }) {
  return (
    <div className="flex-1 flex items-center justify-center py-16">
      <div className="text-sm text-text-muted animate-pulse">
        Loading{label ? ` ${label}` : ''}…
      </div>
    </div>
  )
}


export function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-border bg-surface-secondary p-4">
      <h3 className="text-sm font-semibold text-text-primary mb-3">{title}</h3>
      {children}
    </section>
  )
}

// Color-coded pill for the import row's source.  Compact (no text on
// the pill itself — the abbreviation lives in the title attribute) so
// it sits inline with the project name without crowding the row.
//   claude-code → orange (Anthropic)
//   chatgpt     → green  (OpenAI)
//   other       → gray   (forward-compatible for future sources)
export function SourceBadge({ source }: { source: string }) {
  const cfg: Record<string, { label: string; cls: string; full: string }> = {
    'claude-code': {
      label: 'CC',
      cls: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
      full: 'Claude Code',
    },
    'chatgpt': {
      label: 'GPT',
      cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
      full: 'ChatGPT',
    },
  }
  const c = cfg[source] || {
    label: (source || '?').slice(0, 3).toUpperCase(),
    cls: 'bg-text-muted/15 text-text-muted border-border',
    full: source || 'unknown source',
  }
  return (
    <span
      title={c.full}
      className={`inline-flex shrink-0 items-center justify-center text-[9px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded border ${c.cls}`}
    >
      {c.label}
    </span>
  )
}

