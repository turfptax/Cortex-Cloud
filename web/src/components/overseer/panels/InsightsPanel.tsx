import { TokenChip } from './WorkingMemoryView'
import {
  type PendingInterpretation,
  type InsightScanRow,
  type InsightPendingResp,
  fmtRelative,
} from '../shared'

// ── Slice 3h: Insights review queue ─────────────────────────

export function InsightsPanel({
  interpretations,
  counts,
  scans,
  statusFilter,
  setStatusFilter,
  scanProject,
  setScanProject,
  scanDays,
  setScanDays,
  onScanNow,
  onDistillCorrections,
  onDecide,
  editingId,
  setEditing,
  editTitle,
  editBody,
  setEditTitle,
  setEditBody,
  onTokenClick,
  busy,
}: {
  interpretations: PendingInterpretation[]
  counts?: InsightPendingResp['counts']
  scans: InsightScanRow[]
  statusFilter: string
  setStatusFilter: (s: string) => void
  scanProject: string
  setScanProject: (p: string) => void
  scanDays: number
  setScanDays: (d: number) => void
  onScanNow: () => void
  onDistillCorrections: () => void
  onDecide: (
    id: number,
    decision: 'confirm' | 'reject' | 'edit-and-confirm',
    overrides?: { edit_title?: string; edit_body?: string; review_note?: string },
  ) => void
  editingId: number | null
  setEditing: (id: number | null, title: string, body: string) => void
  editTitle: string
  editBody: string
  setEditTitle: (t: string) => void
  setEditBody: (b: string) => void
  onTokenClick: (token: string) => void
  busy: string
}) {
  const kindBadgeClass = (kind: string) => {
    if (kind === 'theme') return 'bg-accent/20 text-accent-hover'
    if (kind === 'pattern') return 'bg-amber-500/20 text-amber-400'
    if (kind === 'drift') return 'bg-success/20 text-success'
    if (kind === 'blindspot') return 'bg-red-500/20 text-red-400'
    return 'bg-surface-tertiary text-text-muted'
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-5xl mx-auto space-y-6">
        <div>
          <h3 className="text-base font-semibold text-text-primary">
            Proposed insights (Sonnet → human review)
          </h3>
          <p className="text-xs text-text-muted mt-1">
            The overseer scans recent gist arcs per project and proposes
            new themes / patterns / drift it sees emerging. Nothing
            applies until you confirm. Reject the noise. Edit the title
            or body if a candidate is real but the framing is off.
          </p>
        </div>

        {/* Scan trigger */}
        <div className="bg-surface-secondary border border-border rounded-lg p-4">
          <div className="text-xs uppercase tracking-wide text-text-muted mb-2">
            Trigger a scan
          </div>
          <div className="flex items-baseline gap-2 flex-wrap">
            <input
              type="text"
              value={scanProject}
              onChange={(e) => setScanProject(e.target.value)}
              placeholder="project tag (e.g. UFOSINT)"
              className="px-3 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded-md border border-border focus:outline-none focus:border-accent w-56"
            />
            <input
              type="number"
              value={scanDays}
              onChange={(e) => setScanDays(parseInt(e.target.value) || 7)}
              min={1}
              max={90}
              className="px-3 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded-md border border-border focus:outline-none focus:border-accent w-16"
            />
            <span className="text-xs text-text-muted">days</span>
            <button
              onClick={onScanNow}
              disabled={!!busy || !scanProject.trim()}
              className="ml-2 px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
            >
              {busy ? busy : 'Scan now'}
            </button>
          </div>
          <p className="text-[10px] text-text-muted mt-2 italic">
            Single Sonnet call; cost capped at $0.05/scan. Cheap projects
            run for fractions of a cent. The auto-loop also scans up to
            2 active+human projects per tick (24h cadence per project).
          </p>
        </div>

        {/* 3i CP2: distill corrections → blindspot proposals */}
        <div className="bg-surface-secondary border border-border rounded-lg p-4">
          <div className="text-xs uppercase tracking-wide text-text-muted mb-2">
            Distill corrections → blindspots
          </div>
          <p className="text-[11px] text-text-secondary mb-3 leading-relaxed">
            User corrections (from chat, dialectic resolutions, or manual
            log) are clustered by Sonnet into blindspot candidates that
            land in this same review queue with kind=blindspot. The
            auto-loop runs this once per 24h if there are at least 3
            uncondidated corrections.
          </p>
          <button
            onClick={onDistillCorrections}
            disabled={!!busy}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
          >
            {busy ? busy : 'Distill now'}
          </button>
        </div>

        {/* Recent scans (auto-loop visibility) */}
        {scans.length > 0 && (
          <details className="bg-surface-secondary border border-border rounded-lg p-4">
            <summary className="cursor-pointer text-xs uppercase tracking-wide text-text-muted">
              Recent scans ({scans.length})
            </summary>
            <ul className="mt-3 space-y-1 text-[11px]">
              {scans.map((s) => {
                const okColor = s.ok
                  ? (s.candidates_proposed > 0
                      ? 'text-success'
                      : 'text-text-muted')
                  : 'text-red-400'
                return (
                  <li
                    key={s.id}
                    className="grid grid-cols-[max-content_max-content_1fr_max-content_max-content] gap-x-3 items-baseline"
                  >
                    <span className="text-text-muted text-[10px]">
                      {fmtRelative(s.scanned_at)}
                    </span>
                    <span className="text-[9px] uppercase tracking-wide text-text-muted">
                      {s.triggered_by}
                    </span>
                    <span className="text-text-secondary truncate">
                      {s.project || s.scan_kind}
                    </span>
                    <span className={okColor}>
                      {s.ok
                        ? `${s.candidates_proposed} new` +
                          (s.candidates_deduped > 0
                            ? ` (+${s.candidates_deduped} dup)`
                            : '')
                        : `error: ${(s.error || '').slice(0, 40)}`}
                    </span>
                    <span className="text-text-muted text-[10px]">
                      {s.cost_usd > 0 ? `$${s.cost_usd.toFixed(4)}` : '$0'}
                      {s.error && s.error.includes('insufficient') && ' · skipped'}
                    </span>
                  </li>
                )
              })}
            </ul>
          </details>
        )}

        {/* Status filter pills */}
        <div className="flex items-center gap-1 bg-surface-secondary border border-border rounded-lg p-1 w-fit">
          {(['pending', 'confirmed', 'edited', 'rejected'] as const).map((s) => {
            const n = counts?.[s] ?? 0
            return (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer ${
                  statusFilter === s
                    ? 'bg-accent text-white'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                {s} {n > 0 && <span className="text-[10px] opacity-80">({n})</span>}
              </button>
            )
          })}
        </div>

        {/* Candidate list */}
        {interpretations.length === 0 ? (
          <div className="text-sm text-text-muted italic">
            {statusFilter === 'pending'
              ? 'No pending candidates. Run a scan above to propose some.'
              : `No ${statusFilter} candidates.`}
          </div>
        ) : (
          <ul className="space-y-3">
            {interpretations.map((it) => (
              <li
                key={it.id}
                className="bg-surface-secondary border border-border rounded-lg p-4 space-y-2"
              >
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className={`text-[10px] uppercase font-bold px-1.5 py-0.5 rounded ${kindBadgeClass(it.kind)}`}>
                    {it.kind}
                  </span>
                  {it.direction && (
                    <span className="text-[10px] uppercase text-text-muted">
                      {it.direction}
                    </span>
                  )}
                  <span className="text-[10px] uppercase text-text-muted">
                    [{it.confidence}]
                  </span>
                  {/* 3h CP2: source-kind badge so chat-sourced
                      candidates are visually distinct from loop scans */}
                  <span
                    className={`text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded font-mono ${
                      it.source_kind === 'chat-snippet'
                        ? 'bg-accent-hover/20 text-accent-hover'
                        : 'bg-surface-tertiary text-text-muted'
                    }`}
                    title={
                      it.source_kind === 'chat-snippet'
                        ? `From overseer chat reply (msg #${(it as any).source_chat_message_id ?? '?'})`
                        : `From a periodic ${it.source_kind} scan`
                    }
                  >
                    {it.source_kind}
                  </span>
                  {it.source_project && (
                    <span className="text-[10px] uppercase text-text-secondary">
                      project: {it.source_project}
                    </span>
                  )}
                  <span className="text-[10px] text-text-muted ml-auto">
                    proposed {fmtRelative(it.proposed_at)}
                  </span>
                </div>

                {editingId === it.id ? (
                  <div className="space-y-2">
                    <input
                      type="text"
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      className="w-full px-3 py-2 bg-surface-tertiary text-text-primary text-sm rounded border border-accent/40 focus:outline-none focus:border-accent"
                    />
                    <textarea
                      value={editBody}
                      onChange={(e) => setEditBody(e.target.value)}
                      rows={4}
                      className="w-full px-3 py-2 bg-surface-tertiary text-text-secondary text-xs rounded border border-accent/40 focus:outline-none focus:border-accent leading-relaxed"
                    />
                  </div>
                ) : (
                  <div>
                    <div className="text-sm text-text-primary font-medium">
                      {it.title}
                    </div>
                    <p className="text-xs text-text-secondary mt-1 leading-relaxed">
                      {it.body}
                    </p>
                  </div>
                )}

                {/* 3i CP2: blindspot-kind structured fields */}
                {it.kind === 'blindspot' && editingId !== it.id && (
                  <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-[11px] mt-2 bg-surface-tertiary/30 p-2 rounded">
                    <dt className="text-text-muted uppercase tracking-wide text-[10px]">
                      model:
                    </dt>
                    <dd className="text-text-primary font-mono">
                      {it.bs_model_pattern || '*'}
                    </dd>
                    <dt className="text-text-muted uppercase tracking-wide text-[10px]">
                      topic:
                    </dt>
                    <dd className="text-text-secondary font-mono">
                      {it.bs_topic_pattern || '(any)'}
                    </dd>
                    <dt className="text-text-muted uppercase tracking-wide text-[10px]">
                      conf adj:
                    </dt>
                    <dd
                      className={`font-medium ${
                        (it.bs_confidence_adjustment ?? 0) > 0
                          ? 'text-amber-400'
                          : (it.bs_confidence_adjustment ?? 0) < 0
                            ? 'text-text-muted'
                            : 'text-text-secondary'
                      }`}
                    >
                      {(it.bs_confidence_adjustment ?? 0) > 0
                        ? `+${it.bs_confidence_adjustment} (treat reported as too high)`
                        : (it.bs_confidence_adjustment ?? 0) < 0
                          ? `${it.bs_confidence_adjustment} (treat reported as too low)`
                          : '0 (no adjustment)'}
                    </dd>
                  </dl>
                )}

                {it.rationale && (
                  <details className="text-[11px] text-text-muted">
                    <summary className="cursor-pointer uppercase tracking-wide">
                      Rationale
                    </summary>
                    <p className="mt-1 leading-relaxed">{it.rationale}</p>
                  </details>
                )}

                {/* Source pointer ids — meaning depends on kind:
                    - blindspot → correction ids (not drillable today)
                    - everything else → gist ids (clickable token chips) */}
                {(() => {
                  let ids: number[] = []
                  try {
                    ids = JSON.parse(it.source_pointer_ids || '[]')
                  } catch {}
                  if (ids.length === 0) return null
                  const isBlindspot = it.kind === 'blindspot'
                  const label = isBlindspot ? 'Corrections:' : 'Source:'
                  return (
                    <div className="flex items-baseline gap-1.5 flex-wrap text-[10px]">
                      <span className="uppercase tracking-wide text-text-muted">
                        {label}
                      </span>
                      {ids.slice(0, 12).map((sid) =>
                        isBlindspot ? (
                          <span
                            key={sid}
                            className="text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-surface-tertiary text-text-muted font-mono"
                            title="Correction row id (not drillable today)"
                          >
                            c:{sid}
                          </span>
                        ) : (
                          <TokenChip
                            key={sid}
                            token={`g:${sid}`}
                            onClick={onTokenClick}
                          />
                        ),
                      )}
                      {ids.length > 12 && (
                        <span className="text-text-muted">
                          +{ids.length - 12} more
                        </span>
                      )}
                    </div>
                  )
                })()}

                {/* Action row */}
                {it.status === 'pending' ? (
                  <div className="flex items-center gap-2 pt-2 border-t border-border">
                    {editingId === it.id ? (
                      <>
                        <button
                          onClick={() =>
                            onDecide(it.id, 'edit-and-confirm', {
                              edit_title: editTitle,
                              edit_body: editBody,
                            })
                          }
                          disabled={!!busy}
                          className="px-3 py-1.5 rounded-md text-xs font-medium bg-success/80 hover:bg-success text-white cursor-pointer disabled:opacity-50"
                        >
                          Confirm edited
                        </button>
                        <button
                          onClick={() => setEditing(null, '', '')}
                          disabled={!!busy}
                          className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
                        >
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => onDecide(it.id, 'confirm')}
                          disabled={!!busy}
                          className="px-3 py-1.5 rounded-md text-xs font-medium bg-accent hover:bg-accent-hover text-white cursor-pointer disabled:opacity-50"
                        >
                          Confirm
                        </button>
                        <button
                          onClick={() => setEditing(it.id, it.title, it.body)}
                          disabled={!!busy}
                          className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => onDecide(it.id, 'reject')}
                          disabled={!!busy}
                          className="px-3 py-1.5 rounded-md text-xs font-medium text-text-muted hover:text-red-400 cursor-pointer disabled:opacity-50"
                        >
                          Reject
                        </button>
                      </>
                    )}
                  </div>
                ) : (
                  <div className="text-[10px] text-text-muted pt-2 border-t border-border">
                    {it.status} {it.reviewed_by && `by ${it.reviewed_by}`}
                    {it.applied_table && (
                      <> → landed in <span className="font-mono">{it.applied_table}#{it.applied_id}</span></>
                    )}
                    {it.review_note && <> · "{it.review_note}"</>}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

